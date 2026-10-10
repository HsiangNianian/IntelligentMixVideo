"""文案切片：共用入口记录日志，业务完成字符对齐、模型切分与时间投射。"""

import bisect
from collections import Counter
import json
import logging
import re
from time import perf_counter
import unicodedata
from urllib.parse import urlparse

from pydantic import ValidationError
from openai import APIError, APITimeoutError, OpenAI

from .settings import ClientSettings, Settings

# 对齐忽略常见中英文标点；字幕过滤集合供下游合成使用，切片原文保留标点。
PUNCTUATION = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.!?;:\"'()<>[]{}~`")
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
    ASR 多字。模型只返回分句切点和关键词，时间投射和关键词校验由代码完成。
    显式 config 仅用于当前调用；省略时读取 server/.env 与 IMV_ 环境变量，SDK 在返回前关闭。
    返回 segments（整型 segment_id、秒制 start_time/end_time、group_id、字符串 keyword、level）、
    warnings 和 trace；原文保留标点，字幕去标点由下游合成处理。
    可选 title 与片段共用关键词调用，提供非 null 标题时另返回 title_keyword；非法模型结果直接报错。
    空内容或输出时间错误抛 ValueError，配置或模型输出错误抛
    RuntimeError，内部约束错误抛 AssertionError；ASR 嵌套读取和 SDK 异常原样传播。
    diagnostics 供共用入口记录已完成阶段的详细 trace；返回值只保留原有统计字段。
    """
    trace = diagnostics["trace"]
    # 直接调用须提供约定字段；HTTP 类型校验由路由负责。标点不参与对齐，保留原始下标。
    script = payload["script"]
    title = payload.get("title")
    chars = [
        (i, unicodedata.normalize("NFKC", c).lower())
        for i, c in enumerate(script)
        if not c.isspace() and c not in PUNCTUATION
    ]
    if not chars:
        raise ValueError("文案缺少有效字符。")
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

    # 客户端完整参数覆盖模型字段；服务端 HTTP 策略仍从环境读取，不修改共享状态。
    diagnostics["stage"] = "config"
    try:
        config = Settings(**config.model_dump()) if config is not None else Settings()
    except ValidationError:
        raise RuntimeError("模型或切片配置缺失或不合法，请检查 IMV_ 配置。") from None
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

    # 仅保护原文连续的英文、数字串（含小数、连字符和百分号），不跨空格或中文标点保护。
    diagnostics["stage"] = "candidate_filter"
    offsets = [c[0] for c in chars]
    forbidden = set()
    for token in re.finditer(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*%?", script):
        begin, end = bisect.bisect_left(offsets, token.start()), bisect.bisect_left(offsets, token.end())
        forbidden.update(range(begin + 1, end))
    # 增删修复块内时间为估算值，保留整块以免制造看似精确的切点。
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
    diagnostics["stage"] = "config"
    base_url, key, model = config.llm_base_url, config.llm_api_key, config.llm_model
    try:
        address = urlparse(base_url)
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
    segments, rejected = [], 0
    # 先选切点、提关键词，仅有超长片段时追加语义切分；二次切分校验失败只反馈纠正一次。
    with OpenAI(base_url=base_url, api_key=key, timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
        for stage in ("boundaries", "keywords", "secondary_split"):
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
                    '为短视频素材召回提取关键词，只返回 JSON：{"keywords":[[],["词"],[]]}。'
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
                if title is not None:
                    content = {"title": title, "segments": content}
                    prompt += (
                        '本次输入为含 title 和 segments 的对象，keywords 只对应 segments。'
                        '另从 title 按上述提词规则选一个关键词，返回同级 title_keyword 字符串，'
                        '必须在标题中连续出现；标题为空或无合适关键词时返回空字符串。'
                        '完整输出为 {"keywords":[[],["词"],[]],"title_keyword":"标题关键词"}。'
                    )
            else:
                oversized = [i for i, (a, b) in enumerate(spans) if b - a > segment_max_length]
                if not oversized:
                    continue
                content = [{"text": segments[i]["text"], "keyword": segments[i]["keyword"]} for i in oversized]
                trace["secondary_split_input"] = content
                prompt = (
                    '按语义细分每个输入片段，只返回 JSON：{"cuts":[[5,10],[8]]}。'
                    'cuts 与输入逐项对应，每项列出全部内部切点：从片段开头数的 Unicode 字符数，'
                    '切点位置按原文计算，包含标点和空白，不是字节或词数；位置递增、不重复，不含0及文本末尾。'
                    f'每个子段最多{segment_max_length}个有效字符，长度不计标点和空白；必须包含发音文字。'
                    '优先在标点、完整短语和自然语义边界切分，允许句内切分，不设2秒下限。'
                    f'保留原关键词首次出现位置的完整性；关键词自身超过{segment_max_length}个有效字符时允许拆开。'
                    '英文单词、数字串尽量保持完整，但长度上限优先。不要输出或改写原文，不要解释。'
                )
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
            ]
            started = perf_counter()
            for attempt in range(2 if stage == "secondary_split" else 1):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.2,
                        response_format={"type": "json_object"},
                    )
                finally:
                    trace["model_elapsed_ms"][stage] = round((perf_counter() - started) * 1000, 1)
                raw = ""
                try:
                    if not response.choices or not isinstance(response.choices[0].message.content, str):
                        raise RuntimeError("模型返回空内容。")
                    raw = response.choices[0].message.content.strip()
                    if raw.startswith("```json") and raw.endswith("```"):
                        raw = raw[7:-3].strip()
                    try:
                        output = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        if stage == "secondary_split":
                            raise RuntimeError(f"模型返回非法 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}。") from None
                        raise RuntimeError("模型返回非法 JSON。") from None
                    if not isinstance(output, dict):
                        raise RuntimeError("模型必须返回 JSON 对象。")
                    if stage == "boundaries":
                        ids = output.get("boundaries_after")
                        if not isinstance(ids, list) or any(type(n) is not int or not 1 <= n < len(listing) for n in ids):
                            raise RuntimeError("模型 boundaries_after 必须为有效分句编号数组，不含最后一句。")
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
                        span_groups = [attribution[a] for a, _ in spans]
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
                        if title is not None:
                            title_keyword = output.get("title_keyword")
                            if not isinstance(title_keyword, str) or len(title_keyword) > keyword_max_length or title_keyword not in title:
                                raise RuntimeError("模型 title_keyword 必须为标题中的连续字符串，且不超过12字。")
                        groups = output.get("keywords")
                        if (
                            not isinstance(groups, list)
                            or len(groups) != len(segments)
                            or any(not isinstance(g, list) or any(not isinstance(w, str) for w in g) for g in groups)
                        ):
                            raise RuntimeError("模型关键词数组必须与片段一一对应且元素为字符串。")
                        trace["keyword_candidates"] = groups
                        # 单次扫描选最靠前的有效词；同位置保留首个候选，其余候选均计为拒绝。
                        for item, candidates in zip(segments, groups):
                            keyword, first = "", len(item["text"])
                            for candidate in candidates:
                                word = candidate.strip()
                                start = item["text"].find(word)
                                if word and len(word) <= keyword_max_length and 0 <= start < first:
                                    keyword, first = word, start
                            item["keyword"] = keyword
                            rejected += len(candidates) - bool(item["keyword"])
                            # 有关键词即重点句 2，否则为普通句 1；CTA 需语义判断，不标注。
                            item["level"] = 2 if item["keyword"] else 1
                    else:
                        cuts = output.get("cuts")
                        trace["secondary_split_cuts"] = cuts
                        if not isinstance(cuts, list) or len(cuts) != len(oversized):
                            raise RuntimeError(f"二次切分 cuts 必须为数组并与 {len(oversized)} 个输入片段一一对应。")
                        planned = dict(zip(oversized, cuts))
                        refined, refined_groups, cursor = [], [], 0
                        for index, item in enumerate(segments):
                            text, keyword = item["text"], item["keyword"]
                            points = planned.get(index, [])
                            if (not isinstance(points, list)
                                or any(type(p) is not int or not 0 < p < len(text) for p in points)
                                or points != sorted(set(points))):
                                raise RuntimeError(f"二次切分输入 {oversized.index(index) + 1} 的切点 {points!r} 不合法：须为严格递增、不重复的整数数组，且 0 < 切点 < {len(text)}。")
                            edges = [0, *points, len(text)]
                            keyword_start = text.find(keyword) if keyword else -1
                            keyword_length = sum(not c.isspace() and c not in PUNCTUATION for c in keyword)
                            if 0 < keyword_length <= segment_max_length and any(keyword_start < p < keyword_start + len(keyword) for p in points):
                                raise RuntimeError(f"二次切分输入 {oversized.index(index) + 1} 的切点 {points!r} 拆开了关键词，不能在 ({keyword_start}, {keyword_start + len(keyword)}) 内切分。")
                            for begin, end in zip(edges, edges[1:]):
                                a, b = bisect.bisect_left(offsets, cursor + begin), bisect.bisect_left(offsets, cursor + end)
                                if b - a > segment_max_length or a == b:
                                    raise RuntimeError(f"二次切分输入 {oversized.index(index) + 1} 的子段 [{begin}, {end}) 有 {b - a} 个有效字符，要求 1～{segment_max_length} 个。")
                                inherited = keyword if begin <= keyword_start and keyword_start + len(keyword) <= end else ""
                                refined.append({**item, "text": text[begin:end], "keyword": inherited,
                                                "level": 2 if inherited else 1,
                                                "start_time_ms": round(starts[a]), "end_time_ms": round(ends[b - 1])})
                                refined_groups.append(attribution[a])
                            cursor += len(text)
                        segments, span_groups = refined, refined_groups
                except RuntimeError as exc:
                    if stage != "secondary_split" or attempt == 1:
                        raise
                    trace["secondary_split_validation_error"] = str(exc)
                    messages = [*messages,
                        *([{"role": "assistant", "content": raw}] if raw else []),
                        {"role": "user", "content": f"校验失败：{exc} 请根据原始输入修正，并重新返回完整 cuts JSON。"},
                    ]
                    continue
                break

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
