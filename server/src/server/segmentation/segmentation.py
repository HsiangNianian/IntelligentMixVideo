"""文案切片与标题提词：无 ASR 时仅提取标题词，其余完成字符对齐、切分与时间投射；
另提供独立的 translate，把切片结果一次性译成英文并校验英文关键词。"""

import bisect
from collections import Counter
import json
import logging
import re
from time import perf_counter
import unicodedata
from urllib.parse import urlparse

import jieba
from pydantic import ValidationError
from openai import APIError, APITimeoutError, OpenAI

from .settings import ClientSettings, Settings

jieba.setLogLevel(logging.WARNING)

# 对齐忽略常见中英文标点；字幕过滤集合供下游合成使用，切片原文保留标点。
PUNCTUATION = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.．!?;:\"'()<>[]{}~`")
SUBTITLE_PUNCTUATION = PUNCTUATION - {"？", "?"}


def segmentation_error(exc: Exception) -> tuple[str, int]:
    """日志与 HTTP 共用错误摘要和状态码；模型异常不回显供应商响应或凭据。"""
    if isinstance(exc, APITimeoutError):
        return "模型请求超时。", 504
    if isinstance(exc, APIError):
        return "模型服务请求失败。", 502
    if isinstance(exc, (ValueError, RuntimeError, AssertionError)):
        status = 422 if isinstance(exc, ValueError) else 500 if isinstance(exc, AssertionError) else 502
        return str(exc), status
    return type(exc).__name__, 500


def _settings(config: ClientSettings | None) -> Settings:
    """准备本次调用的模型配置并检查，返回 Settings。

    传了 config（客户端配置）就用它，传 None 就读服务端 server/.env 与 IMV_ 环境变量；
    「是否允许远程 HTTP」始终只看服务端设置。必填项缺失或格式不对、模型地址不是有效的
    http/https 地址、或用远程 http 地址却没在服务端开启允许时，抛 RuntimeError，报错不带原始输入。
    """
    try:
        config = Settings(**config.model_dump()) if config is not None else Settings()
    except ValidationError:
        raise RuntimeError("模型或切片配置缺失或不合法，请检查 IMV_ 配置。") from None
    try:
        address = urlparse(config.llm_base_url)
    except ValueError:
        raise RuntimeError("模型地址格式不合法。") from None
    if (
        address.scheme not in ("https", "http")
        or not address.netloc
        or (
            address.scheme == "http"
            and address.hostname not in ("localhost", "127.0.0.1", "::1")
            and not config.allow_insecure_llm_http
        )
    ):
        raise RuntimeError("模型地址必须有效，远程 HTTP 需要显式授权。")
    return config


def _json_output(response) -> dict:
    """把模型回复的文字解析成 dict。

    回复整段被 ```json … ``` 包住时先去掉包裹。回复为空、不是合法 JSON、或解析出来不是对象
    （例如是数组）时抛 RuntimeError。
    """
    if not response.choices or not isinstance(response.choices[0].message.content, str):
        raise RuntimeError("模型返回空内容。")
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[7:-3].strip()
    try:
        output = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("模型返回非法 JSON。") from None
    if not isinstance(output, dict):
        raise RuntimeError("模型必须返回 JSON 对象。")
    return output


def _pick_keyword(text: str, candidates: list[str], max_length: int | None) -> str:
    """从模型给的候选词里挑出 text 的关键词，没有合格的返回空字符串。

    候选去掉首尾空白后，同时满足以下三条才合格：不为空；长度不超过 max_length（None 表示不限）；
    在 text 中原样连续出现（大小写、全半角都要一致）。多个候选合格时取在 text 中位置最靠前的，
    位置相同取先给出的。例如 text 为「散养的土鸡。」、候选为 ["土鸡", "散养的土鸡"] 时返回「散养的土鸡」。
    """
    keyword, first = "", len(text)
    for candidate in candidates:
        word = candidate.strip()
        start = text.find(word)
        if word and (max_length is None or len(word) <= max_length) and 0 <= start < first:
            keyword, first = word, start
    return keyword


def _check_title_keyword(keyword, title: str, max_length: int | None, field: str) -> str:
    """检查模型给的标题关键词，合格就原样返回，不合格就抛 RuntimeError（报错里写明字段名 field）。

    合格条件：是字符串；长度不超过 max_length（None 表示不限）；在 title 中原样连续出现。
    空字符串表示没有关键词，视为合格。和片段关键词不同，这里不合格不置空，而是整体失败。
    """
    if not isinstance(keyword, str) or (max_length is not None and len(keyword) > max_length) or keyword not in title:
        limit = f"，且不超过{max_length}字" if max_length is not None else ""
        raise RuntimeError(f"模型 {field} 必须为标题中的连续字符串{limit}。")
    return keyword


def segment(payload: dict, *, config: ClientSettings | None = None, diagnostics: dict | None = None) -> dict:
    """HTTP 与合成任务共用入口：记录阶段诊断，保留返回值和原异常，不记录凭据。"""
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.update(stage="input", trace={})
    logger = logging.getLogger("uvicorn.error")
    try:
        result = _segment(payload, config=config, diagnostics=diagnostics)
    except Exception as exc:
        message, status = segmentation_error(exc)
        logger.warning("切片失败 stage=%s status=%s error=%s trace=%s",
                       diagnostics["stage"], status, message, diagnostics["trace"])
        raise
    logger.info("切片成功 trace=%s", diagnostics["trace"])
    return result


def _segment(payload: dict, *, config: ClientSettings | None, diagnostics: dict) -> dict:
    """用正确文案和 ASR 词级时间生成片段；不调用 TTS/ASR，不降级模型失败。

    请求为 {script, asr_result}，读取 fun-asr transcripts 第一音轨，词时间为 begin_time/end_time 毫秒。
    替换/增删代价均为 1；波前搜索保留最远位置，平局依次优先替换、文案多字、
    ASR 多字。模型只返回分句切点和关键词，超长片段按安全边界切分，时间投射和关键词校验由代码完成。
    显式 config 仅用于当前调用；省略时读取 server/.env 与 IMV_ 环境变量，SDK 在返回前关闭。
    返回 segments（整型 segment_id、秒制 start_time/end_time、group_id、字符串 keyword、level）、
    warnings 和 trace；原文保留标点，字幕去标点由下游合成处理。
    有 ASR 时 title 与片段共用关键词调用；无 ASR 时仅调用一次模型，返回 title_keyword，不校验提词内容。
    空内容或输出时间错误抛 ValueError，配置或模型输出错误抛
    RuntimeError，内部约束错误抛 AssertionError；ASR 嵌套读取和 SDK 异常原样传播。
    diagnostics 供共用入口记录已完成阶段的详细 trace；返回值只保留原有统计字段。
    """
    trace = diagnostics["trace"]
    title = payload.get("title")
    title_only = payload.get("asr_result") is None and title is not None
    if not title_only:
        # HTTP 类型校验由路由负责；有 ASR 时仍须有效文案，标点不参与对齐。
        script = payload.get("script") or ""
        chars = [
            (i, unicodedata.normalize("NFKC", c).lower())
            for i, c in enumerate(script)
            if not c.isspace() and c not in PUNCTUATION
        ]
        if not chars:
            raise ValueError("文案缺少有效字符。")
        if payload.get("asr_result") is None:
            raise ValueError("缺少 ASR 结果或标题。")
    # 客户端完整参数覆盖模型字段；服务端 HTTP 策略仍从环境读取，不修改共享状态。
    diagnostics["stage"] = "config"
    config = _settings(config)
    base_url, key, model = config.llm_base_url, config.llm_api_key, config.llm_model
    if title_only:
        diagnostics["stage"] = "keywords"
        with OpenAI(base_url=base_url, api_key=key, timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
            response = client.chat.completions.create(
                model=model, temperature=0.2, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": '从标题中提取一个关键词，只返回 JSON：{"title_keyword":"关键词"}，无合适词时返回空字符串。'},
                          {"role": "user", "content": title}],
            )
        logging.getLogger("uvicorn.error").info(
            "切片模型输出 stage=title_keywords attempt=1 content=%r",
            response.choices[0].message.content if response.choices else None)
        try:
            keyword = json.loads(response.choices[0].message.content).get("title_keyword", "")
            return {"title_keyword": keyword if isinstance(keyword, str) else ""}
        except (AttributeError, IndexError, TypeError, ValueError):
            raise RuntimeError("模型标题提词响应格式无效。") from None
    diagnostics["stage"] = "input"
    # ponytail: MVP 信任上游 Fun-ASR 结构与词时间，只取第一音轨；接入其他来源时再扩展校验。
    sentences = payload["asr_result"]["transcripts"][0]["sentences"]
    # timeline_sentences 与 timeline 下标一一对应，记录每个字符所属的 ASR 句序号。
    timeline, timeline_sentences = [], []
    for sentence_index, sentence in enumerate(sentences):
        for word in sentence["words"]:
            begin, end = word["begin_time"], word["end_time"]
            # ponytail: 词内字符均分词时长，并非真实字级强制对齐；精度不足时需上游提供更细时间轴。
            content = [c for c in word["text"] if not c.isspace() and c not in PUNCTUATION]
            # 空内容不进入循环或执行除法。
            for i, char in enumerate(content):
                timeline.append(
                    (
                        unicodedata.normalize("NFKC", char).lower(),
                        begin + (end - begin) * i / len(content),
                        begin + (end - begin) * (i + 1) / len(content),
                    )
                )
                timeline_sentences.append(sentence_index)
    if not timeline:
        raise ValueError("ASR 缺少有效发音字符。")

    # 首次候选至少 2 秒；二次切分复用对齐字符计数，标点和空白不占长度。
    minimum, keyword_max_length, segment_max_length = 2000, 12, 8
    diagnostics["stage"] = "alignment"
    # 先剥离相同前后缀，仅对中间差异搜索并回溯。
    prefix = suffix = 0
    limit = min(len(chars), len(timeline))
    for backwards in (False, True):
        while prefix + suffix < limit:
            index = -1 - suffix if backwards else prefix
            if chars[index][1] != timeline[index][0]:
                break
            if backwards:
                suffix += 1
            else:
                prefix += 1
    left = chars[prefix : len(chars) - suffix]
    right = timeline[prefix : len(timeline) - suffix]
    rows, columns = len(left), len(right)
    middle = []
    if not rows or not columns:
        middle = [("script_extra", prefix + i, None) for i in range(rows)]
        middle += [("asr_extra", None, prefix + j) for j in range(columns)]
    else:
        # ponytail: 不设工作预算，O(D²) 回溯状态随差异增大；内存成为瓶颈时再改线性空间回溯。
        history, previous, reached = [], {}, False
        for distance in range(max(rows, columns) + 1):
            current = {}
            for diagonal in range(max(-distance, -columns), min(distance, rows) + 1):
                start, kind = (0, "match") if distance == 0 else (-1, "match")
                for operation, prior_diagonal, step in (
                    ("substitution", diagonal, 1),
                    ("script_extra", diagonal - 1, 1),
                    ("asr_extra", diagonal + 1, 0),
                ):
                    prior = previous.get(prior_diagonal)
                    if prior is not None:
                        candidate = prior[0] + step
                        if candidate <= rows and 0 <= candidate - diagonal <= columns and candidate > start:
                            start, kind = candidate, operation
                if start < 0:
                    continue
                i, j = start, start - diagonal
                while i < rows and j < columns:
                    if left[i][1] != right[j][0]:
                        break
                    i, j = i + 1, j + 1
                current[diagonal] = (i, start, kind)
                if i == rows and j == columns:
                    reached = True
                    break
            history.append(current)
            previous = current
            if reached:
                break
        if not reached:
            raise AssertionError("对齐未到达终点。")
        # 每层保存最远位置及其操作，从终点回溯得到逐字符对应关系。
        for layer in reversed(history):
            end, start, kind = layer[diagonal]
            middle.extend(("match", prefix + i, prefix + i - diagonal) for i in range(end - 1, start - 1, -1))
            if kind == "substitution":
                middle.append((kind, prefix + start - 1, prefix + start - diagonal - 1))
            elif kind == "script_extra":
                middle.append((kind, prefix + start - 1, None))
                diagonal -= 1
            elif kind == "asr_extra":
                middle.append((kind, None, prefix + start - diagonal - 1))
                diagonal += 1
        middle.reverse()
    ops = [("match", i, i) for i in range(prefix)] + middle
    ops += [("match", len(chars) - suffix + i, len(timeline) - suffix + i) for i in range(suffix)]
    counts = Counter(kind for kind, _, _ in ops)
    trace.update(
        matched_chars=counts["match"], substitution_chars=counts["substitution"],
        script_extra_chars=counts["script_extra"], asr_extra_chars=counts["asr_extra"],
        edit_cost=len(ops) - counts["match"],
    )
    # 分母覆盖两侧文本，避免 ASR 大量多字仍被视为文案完全匹配。
    ratio = counts["match"] / max(len(chars), len(timeline))
    warnings = []
    if ratio < 0.9:
        warnings.append({"code": "low_alignment_match_ratio", "message": "文案与 ASR 存在较多差异。"})

    # 替换直接继承时间；增删连续段向两侧扩一字，合并后仅在块内均分时间。
    starts, ends = [0.0] * len(chars), [0.0] * len(chars)
    blocks, run = [], None
    for position, (kind, i, j) in enumerate([*ops, ("match", None, None)]):
        if i is not None and j is not None:
            starts[i], ends[i] = timeline[j][1:]
        if kind in ("script_extra", "asr_extra"):
            if run is None:
                run = position
        elif run is not None:
            begin, end = max(0, run - 1), min(len(ops), position + 1)
            if blocks and begin <= blocks[-1][1]:
                blocks[-1] = (blocks[-1][0], end)
            else:
                blocks.append((begin, end))
            run = None
    repair_ranges = []
    for begin, end in blocks:
        indices = [i for _, i, _ in ops[begin:end] if i is not None]
        sources = [j for _, _, j in ops[begin:end] if j is not None]
        if not indices:
            continue
        if not sources:
            raise ValueError("修复块缺少可继承的 ASR 时间。")
        begin_time, end_time = timeline[sources[0]][1], timeline[sources[-1]][2]
        step = (end_time - begin_time) / len(indices)
        for order, i in enumerate(indices):
            starts[i], ends[i] = begin_time + step * order, begin_time + step * (order + 1)
        repair_ranges.append((indices[0], indices[-1] + 1))
    trace["repair_block_count"] = len(repair_ranges)

    # 两次切分共用词串保护，数字后紧跟三位数字的半角逗号视为千分位；
    # 逗号列表须有共享后缀或明确约数语境，避免把“99，3件”连成一串。
    diagnostics["stage"] = "candidate_filter"
    offsets = [c[0] for c in chars]
    protected = set()
    number = r"\d+(?:[.．]\d+)?"
    for pattern in (
        r"[+＋\-－]?[A-Za-zＡ-Ｚａ-ｚ\d]+(?:(?:[.．\-－]|(?<=\d),(?=\d{3}(?!\d)))[A-Za-zＡ-Ｚａ-ｚ\d]+)*[%％]?",
        rf"{number}(?:\s*[,，、]\s*{number})+\s*后",
        rf"第\s*{number}(?:\s*[,，、]\s*{number})+\s*名",
        rf"{number}(?:\s*、\s*{number})+",
        rf"(?<=等)\s*{number}\s*[,，]\s*{number}\s*(?:小时|分钟|秒|天|周|个月|月|年)",
    ):
        for token in re.finditer(pattern, script):
            begin, end = bisect.bisect_left(offsets, token.start()), bisect.bisect_left(offsets, token.end())
            protected.update(range(begin + 1, end))
    # 增删修复块内时间为估算值，保留整块以免制造看似精确的切点。
    forbidden = protected.copy()
    for begin, end in repair_ranges:
        forbidden.update(range(begin + 1, end))
    # 首次只使用中文标点候选；无候选或全文不足 2 秒时保持整段，超长片段随后二次切分。
    # 从左到右保留切点，两侧均留足 2 秒；过滤只隐藏边界，不删除原文。
    # offset 是原文中标点后的字符位置；多个限制同时命中时记录首个过滤原因。
    candidate_edges, filtered_boundaries = [0], []
    for clause in re.finditer(r"[^，。！？；：、…]*[，。！？；：、…]+|[^，。！？；：、…]+$", script):
        cut = bisect.bisect_left(offsets, clause.end())
        if not 0 < cut < len(chars):
            continue
        if cut in forbidden:
            reason = "protected_or_repaired"
        elif ends[cut - 1] - starts[candidate_edges[-1]] < minimum:
            reason = "min_duration_before"
        elif ends[-1] - starts[cut] < minimum:
            reason = "min_duration_after"
        else:
            candidate_edges.append(cut)
            continue
        filtered_boundaries.append({"offset": clause.end(), "reason": reason})
    candidate_edges.append(len(chars))
    text_edges = [0, *[offsets[i] for i in candidate_edges[1:-1]], len(script)]
    listing = [
        {"id": i, "text": script[a:b]}
        for i, (a, b) in enumerate(zip(text_edges, text_edges[1:]), 1)
    ]
    trace.update(candidate_clauses=listing, filtered_boundaries=filtered_boundaries,
                 selected_boundaries_after=[], keyword_candidates=[], model_elapsed_ms={})
    segments, rejected = [], 0
    # 模型依次选切点、提关键词；超长片段随后由代码按安全边界切分。
    with OpenAI(base_url=base_url, api_key=key, timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
        for stage in ("boundaries", "keywords"):
            diagnostics["stage"] = stage
            if stage == "boundaries":
                prompt = (
                    '将口播文案切成短句画面，只返回 JSON：{"boundaries_after":[1,3]}。'
                    "数组须列全所有选中的分句编号，升序、不重复，从1开始且不含最后一句；不是只选几个代表性切点。"
                    "在已有分句边界允许的范围内，每段字数上限为10字（不计标点和空白），不设字数下限，不为凑字数合并。"
                    "逐个检查相邻分句，结合主谓宾、状语、补语、定语、并列句和从句，优先保留语义及信息点完整。"
                    "时间节点、条件说明、动作完成、对象说明、结果出现等独立信息点优先单独成段，同一话题或连续卖点也分别判断。"
                    "仅语法不完整、必须依赖相邻句且合并后不超过10字时才合并；已有超长分句保留前后边界，不再合并。"
                    "只用已有分句边界，不生成句内切点；无法在候选边界内满足字数上限时保留原分句。"
                    "候选已按至少2秒和受保护词串过滤，选定后不再合并或拆分；不输出文本、时间、group_id或level。"
                    "输入是标准文案，不是待纠错的ASR文本；不改写、不删字或标点。自检编号范围和顺序；只输出纯JSON，无Markdown或解释。"
                )
                content = listing
            elif stage == "keywords":
                prompt = (
                    '为短视频素材召回提取关键词，只返回 JSON 对象。'
                    "全篇关键词总数不限，逐段判断是否有值得检索画面的具体对象，无合适对象时留空，不凑词。"
                    f"每段最多1个词，每词最多{keyword_max_length}字；"
                    "优先选择本段核心的具体名词或名词短语，如产品、动物、人物、食材、部位、工具、场景。"
                    "保留有助于区分画面的必要修饰语，例如原文连续出现的‘散养的土鸡’；‘蛋清’‘蛋黄’也适合召回。"
                    "不要只提取‘散养’‘新鲜’‘饱满’等动作或属性，也不要选‘囤一点’等营销引导。"
                    "同一对象在不同片段中可重复选择，不为全篇去重而遗漏该段核心对象。"
                    "输出数组长度必须等于输入片段数，第i项只对应第i段，空数组不能省略。"
                    "逐项确认词在该段内连续出现，保留大小写和全半角，不改写、跨标点拼接或借用其他段的词。"
                    "自检段数、每段词数、词长和原文匹配。只输出纯JSON，无Markdown或解释。"
                )
                content = [s["text"] for s in segments]
                example = {"keywords": [[] for _ in segments]}
                if title is not None:
                    content = {"title": title, "segments": content}
                    example["title_keyword"] = ""
                    prompt += (
                        '本次输入为含 title 和 segments 的对象，keywords 只对应 segments。'
                        '另从 title 按上述提词规则选一个关键词，返回同级 title_keyword 字符串，'
                        '必须在标题中连续出现；标题为空或无合适关键词时返回空字符串。'
                    )
                prompt += (
                    f"本次恰有 {len(segments)} 个片段。按以下 JSON 模板逐项填词，"
                    '有关键词的 [] 改为 ["原文关键词"]，无关键词保留 []，不得增删或调换数组项：'
                    + json.dumps(example, ensure_ascii=False)
                )
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
            ]
            started = perf_counter()
            try:
                for attempt in range(6):
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.2,
                        response_format={"type": "json_object"},
                    )
                    raw = response.choices[0].message.content if response.choices else None
                    logging.getLogger("uvicorn.error").info(
                        "切片模型输出 stage=%s attempt=%s content=%r", stage, attempt + 1, raw)
                    try:
                        output = _json_output(response)
                        if stage == "boundaries":
                            ids = output.get("boundaries_after")
                            if not isinstance(ids, list) or any(type(n) is not int or not 1 <= n < len(listing) for n in ids):
                                raise RuntimeError("模型 boundaries_after 必须为有效分句编号数组，不含最后一句。")
                        else:
                            if title is not None:
                                title_keyword = _check_title_keyword(
                                    output.get("title_keyword"), title, keyword_max_length, "title_keyword")
                            groups = output.get("keywords")
                            if (
                                not isinstance(groups, list)
                                or len(groups) != len(segments)
                                or any(not isinstance(g, list) or any(not isinstance(w, str) for w in g) for g in groups)
                            ):
                                raise RuntimeError(
                                    f"模型 keywords 必须为 {len(segments)} 项数组，每项为字符串数组，无关键词时保留空数组。"
                                    f"实际项数：{len(groups) if isinstance(groups, list) else '非数组或缺失'}。")
                        break
                    except RuntimeError as exc:
                        if attempt == 5:
                            raise
                        messages = [*messages,
                                    {"role": "assistant", "content": raw if isinstance(raw, str) else ""},
                                    {"role": "user", "content": f"上次结果校验失败：{exc} 请修正并重新返回完整 JSON 对象。"}]
                        logging.getLogger("uvicorn.error").warning(
                            "切片结果纠错 stage=%s retry=%s error=%s", stage, attempt + 1, exc)
            finally:
                trace["model_elapsed_ms"][stage] = round((perf_counter() - started) * 1000, 1)
            if stage == "boundaries":
                trace["selected_boundaries_after"] = sorted(set(ids))
                edges = [0, *[candidate_edges[n] for n in trace["selected_boundaries_after"]], len(chars)]
                spans = list(zip(edges, edges[1:]))
                # 段落按其首字归属 ASR 句：句内序号从 1 递增，total 为该句的最终段数。
                # 跨句片段整体计入起始句，使同句编号连续且不因归属再切分文本。
                sentence_of_char = [None] * len(chars)
                for _, i, j in ops:
                    if i is not None and j is not None:
                        sentence_of_char[i] = timeline_sentences[j]
                attribution, fallback_sentence = [], None
                for value in sentence_of_char:
                    fallback_sentence = value if value is not None else fallback_sentence
                    attribution.append(fallback_sentence)
                first_sentence = next((s for s in attribution if s is not None), 0)
                attribution = [first_sentence if s is None else s for s in attribution]
                for index, (a, b) in enumerate(spans, 1):
                    begin = 0 if a == 0 else offsets[a]
                    end = len(script) if b == len(chars) else offsets[b]
                    segments.append(
                        {
                            "segment_id": index,
                            "text": script[begin:end],
                            "start_time_ms": round(starts[a]),
                            "end_time_ms": round(ends[b - 1]),
                            "keyword": "",
                            "level": 1,
                        }
                    )
            elif stage == "keywords":
                trace["keyword_candidates"] = groups
                # 单次扫描选最靠前的有效词；同位置保留首个候选，其余候选均计为拒绝。
                for item, candidates in zip(segments, groups):
                    item["keyword"] = _pick_keyword(item["text"], candidates, keyword_max_length)
                    rejected += len(candidates) - bool(item["keyword"])
                    # 有关键词即重点句 2，否则为普通句 1；CTA 需语义判断，不标注。
                    item["level"] = 2 if item["keyword"] else 1

    # 先避免单字子段，再优先标点、结巴词边界及中点；英文数字仅在整串超长时放宽上限。
    # 标点留在前段，关键词仅继承到完整包含首次出现位置的子段；沿用既有字符时间，不重新对齐。
    diagnostics["stage"] = "secondary_split"
    word_edges = {bisect.bisect_left(offsets, end) for _, _, end in jieba.tokenize(script)} if any(
        b - a > segment_max_length for a, b in spans
    ) else set()
    punctuation_edges = {i for i in range(1, len(chars)) if any(
        char in PUNCTUATION for char in script[offsets[i - 1] + 1:offsets[i]]
    )}
    refined, refined_groups = [], []
    for item, (a, b) in zip(segments, spans):
        origin = 0 if a == 0 else offsets[a]
        keyword = item["keyword"]
        keyword_start = origin + item["text"].find(keyword) if keyword else -1
        pending = [(a, b)]
        while pending:
            a, b = pending.pop()
            if b - a > segment_max_length:
                cuts = [cut for cut in range(a + 1, b) if cut not in protected]
                if cuts:
                    middle = min(cuts, key=lambda cut: (
                        min(cut - a, b - cut) < 2,
                        cut not in punctuation_edges, cut not in word_edges, abs(2 * cut - a - b), cut,
                    ))
                    pending.extend([(middle, b), (a, middle)])
                    continue
            begin = 0 if a == 0 else offsets[a]
            end = len(script) if b == len(chars) else offsets[b]
            inherited = keyword if begin <= keyword_start and keyword_start + len(keyword) <= end else ""
            refined.append({**item, "text": script[begin:end], "keyword": inherited,
                            "level": 2 if inherited else 1,
                            "start_time_ms": round(starts[a]), "end_time_ms": round(ends[b - 1])})
            refined_groups.append(attribution[a])
    segments, span_groups = refined, refined_groups

    # 按最终片段重编序号与 ASR 句内分组，二次切分不重新提取关键词。
    group_totals, group_seen = Counter(span_groups), Counter()
    for index, (item, group) in enumerate(zip(segments, span_groups), 1):
        group_seen[group] += 1
        item.update(segment_id=index, group_id=[group_seen[group], group_totals[group]])
    # 保留原始停顿，检查文本覆盖和输出时间。
    diagnostics["stage"] = "output_validation"
    trace.update(segment_count=len(segments), keyword_rejected_count=rejected)
    if "".join(s["text"] for s in segments) != script:
        raise AssertionError("片段未完整覆盖文案。")
    previous_end = 0
    for item in segments:
        if not previous_end <= item["start_time_ms"] < item["end_time_ms"]:
            raise ValueError("ASR 时间精度不足，无法生成合法且不重叠的片段。")
        previous_end = item["end_time_ms"]
    # 输出约定使用秒制起止时间。
    for item in segments:
        item["start_time"] = item.pop("start_time_ms") / 1000
        item["end_time"] = item.pop("end_time_ms") / 1000
    return {
        **({"title_keyword": title_keyword} if title is not None else {}),
        "segments": segments,
        "warnings": warnings,
        "trace": {key: trace[key] for key in (
            "matched_chars", "substitution_chars", "script_extra_chars", "asr_extra_chars",
            "edit_cost", "repair_block_count", "segment_count", "keyword_rejected_count",
        )},
    }


def translate(result: dict, *, title: str | None = None) -> dict:
    """把 segment 的返回结果翻译成英文，只调用一次模型。

    输入：result 是 segment 的返回值；title 是当初传给 segment 的标题，不传就不翻译标题。
    发给模型的只有每个片段的 segment_id、text、keyword，以及 title、title_keyword，时间等字段不发。

    输出：result 的副本，原有字段不变，也不修改传入的 result。每个片段多出 text_en（译文）和
    keyword_en（关键词译文）；传了 title 时顶层多出 title_en 和 title_keyword_en。

    英文关键词的检查与中文相同，只是不限长度：keyword_en 不在本段 text_en 里就置空，
    title_keyword_en 不在 title_en 里就报错；中文关键词为空时英文关键词也是空。
    模型返回的段数对不上、字段类型不对，或译文为空（片段 text_en 为空，或原标题非空而 title_en 为空）时
    抛 RuntimeError，不重试；网络等 SDK 异常原样抛出。
    模型配置只读服务端 server/.env 与 IMV_ 环境变量。
    """
    config = _settings(None)
    segments = result.get("segments", [])
    content = {"segments": [{key: item[key] for key in ("segment_id", "text", "keyword")} for item in segments]}
    prompt = (
        '将短视频口播字幕译成英文，只返回 JSON：{"segments":[{"text_en":"译文","keyword_en":"关键词译文"}]}。'
        "segments 是同一篇文案按顺序切出的连续片段，按顺序连读应是通顺自然的英文；"
        "输出数组长度必须等于输入片段数，第i项只翻译第i段，不合并、拆分或挪动内容，空项不能省略。"
        "keyword 是该段需要高亮的中文关键词：非空时 keyword_en 返回它在本段译文中的对应译法，"
        "必须与 text_en 中的一段连续文字完全相同（含大小写和标点）；keyword 为空时 keyword_en 返回空字符串。"
    )
    if title is not None:
        content.update(title=title, title_keyword=result.get("title_keyword", ""))
        prompt += (
            "输入另含 title 与 title_keyword：同级返回标题译文 title_en 与 title_keyword_en，"
            "title_keyword_en 按同样规则必须在 title_en 中连续出现，title_keyword 为空时返回空字符串。"
            '完整输出为 {"segments":[{"text_en":"","keyword_en":""}],"title_en":"","title_keyword_en":""}。'
        )
    prompt += "自检段数和关键词原文匹配。只输出纯JSON，无Markdown或解释。"
    with OpenAI(base_url=config.llm_base_url, api_key=config.llm_api_key,
                timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
        response = client.chat.completions.create(
            model=config.llm_model, temperature=0.2, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": json.dumps(content, ensure_ascii=False)}],
        )
    output = _json_output(response)
    items = output.get("segments")
    if (
        not isinstance(items, list)
        or len(items) != len(segments)
        or any(not isinstance(i, dict) or not isinstance(i.get("text_en"), str) or not i["text_en"].strip()
               or not isinstance(i.get("keyword_en"), str) for i in items)
    ):
        raise RuntimeError("模型译文数组必须与片段一一对应，text_en 为非空字符串，keyword_en 为字符串。")
    translated = {**result, "segments": [
        {**item, "text_en": i["text_en"],
         "keyword_en": _pick_keyword(i["text_en"], [i["keyword_en"]], None) if item["keyword"] else ""}
        for item, i in zip(segments, items)
    ]}
    if title is not None:
        title_en = output.get("title_en")
        # 原标题有内容时译文不能为空；原标题本身为空时允许空译文。
        if not isinstance(title_en, str) or (title.strip() and not title_en.strip()):
            raise RuntimeError("模型 title_en 必须为字符串，原标题非空时不能为空。")
        keyword = output.get("title_keyword_en") if content["title_keyword"] else ""
        translated.update(title_en=title_en,
                          title_keyword_en=_check_title_keyword(keyword, title_en, None, "title_keyword_en"))
    return translated
