"""文案切片与标题提词：无 ASR 时仅提取标题词，其余完成字符对齐、CT-Punc 恢复并叠加原标点、按句读标点切分与时间投射；
另提供独立的 translate，把切片结果一次性译成英文并校验英文关键词。"""

from collections import Counter
import json
import logging
import re
from time import perf_counter
import unicodedata
from urllib.parse import urlparse

from pydantic import ValidationError
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from ..log_sanitization import sanitize
from .settings import ClientSettings, Settings
from . import punctuation

KEYWORD_MAX_ATTEMPTS = 3
MODEL_MAX_RETRIES = 3

# 历史标点集合供下游字幕过滤使用；切片另通过 Unicode 标点类别识别全部标点。
PUNCTUATION = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.．!?;:\"'()<>[]{}~`")
SUBTITLE_PUNCTUATION = PUNCTUATION - {"？", "?"}


def _is_punctuation(char: str) -> bool:
    """识别去标点及正文对齐使用的字符；是否断句由独立的切点规则判断。"""
    return char in PUNCTUATION or unicodedata.category(char).startswith("P")


def _slice_boundaries(text: str, offsets: list[int]) -> dict[int, int]:
    """将正文切点映射到原文本位置：只切句读，保护数字/缩写，开符号随下段、闭符号随前段。"""
    # 连续句点是省略号；数字中的小数点/千分位、常见称谓和点分缩写不是句末。
    protected = set()
    for match in re.finditer(
        r"[.．]{2,}|(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)(?:[.．]\d+)?|(?<!\d)\d+(?:[.．]\d+)+"
        r"|(?<![A-Za-z])(?:[A-Za-z]{1,3}\.){2,}"
        r"|\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)\.", text, re.IGNORECASE,
    ):
        protected.update(range(match.start(), match.end()))
    openings, quotes = set(), set()
    for i, char in enumerate(text):
        if unicodedata.category(char) in ("Ps", "Pi") or char == "<":
            openings.add(i)
        elif char in "\"'":
            # 英文词内撇号不参与引号配对。
            if char == "'" and i and i + 1 < len(text) and text[i - 1].isalnum() and text[i + 1].isalnum():
                continue
            if char in quotes:
                quotes.remove(char)
            else:
                quotes.add(char)
                openings.add(i)
    boundaries = {0: 0}
    for cut in range(1, len(offsets)):
        begin, end = offsets[cut - 1] + 1, offsets[cut]
        marks = []
        for i in range(begin, end):
            if text[i] not in "，,。！？!?.．" or i in protected:
                continue
            # 英文单词/域名内部的点不按句末处理；无空格的中文句点仍可断句。
            if text[i] in ".．" and i and i + 1 < len(text) and all(
                char.isascii() and char.isalnum() for char in (text[i - 1], text[i + 1])
            ):
                continue
            marks.append(i)
        if marks:
            boundaries[cut] = next((i for i in range(marks[-1] + 1, end) if i in openings), end)
    boundaries[len(offsets)] = len(text)
    return boundaries


def segmentation_error(exc: Exception) -> tuple[str, int]:
    """日志与 HTTP 共用错误摘要和状态码；模型异常不回显供应商响应或凭据。"""
    if isinstance(exc, TimeoutError):
        return "切片处理超时。", 504
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
    「是否允许远程 HTTP」与切片单次上限、总预算始终只看服务端设置。必填项缺失或格式不对、模型地址不是有效的
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


def _response_content(response):
    """兼容端点缺少 choices、message 或 content 时按空回复处理。"""
    choices = getattr(response, "choices", None)
    message = getattr(choices[0], "message", None) if choices else None
    return getattr(message, "content", None)


def _json_output(response) -> dict:
    """把模型回复的文字解析成 dict。

    回复整段被 ```json … ``` 包住时先去掉包裹。回复为空、不是合法 JSON、或解析出来不是对象
    （例如是数组）时抛 RuntimeError。
    """
    raw = _response_content(response)
    if not isinstance(raw, str):
        raise RuntimeError("模型返回空内容。")
    raw = raw.strip()
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


def _thinking(config: Settings) -> dict:
    """模型服务支持 thinking 扩展字段且已开启 llm_disable_thinking 时关闭思考，未开启时不发送该字段。"""
    return {"extra_body": {"thinking": {"type": "disabled"}}} if config.llm_disable_thinking else {}


def _core(text: str) -> str:
    """只保留计数字符（去掉空白与标点），字数上限与切分校验都按它计算。"""
    return "".join(c for c in text if not c.isspace() and not _is_punctuation(c))


def segment(payload: dict, *, config: ClientSettings | None = None, diagnostics: dict | None = None) -> dict:
    """HTTP 与合成任务共用入口：记录阶段诊断，保留返回值和原异常，不记录凭据。"""
    diagnostics = {} if diagnostics is None else diagnostics
    diagnostics.update(stage="input", trace={}, keyword_response=None, model_response=None)
    logger = logging.getLogger("uvicorn.error")
    started = perf_counter()
    try:
        result = _segment(payload, config=config, diagnostics=diagnostics)
    except Exception as exc:
        # 仅失败时记录原始提词正文，并交给合成任务的既有错误日志持久化；不进入接口响应。
        if diagnostics["stage"] == "keywords" and (keyword_response := diagnostics.get("keyword_response")):
            diagnostics["trace"]["keyword_response"] = keyword_response
            exc.keyword_response = keyword_response
        elif diagnostics["model_response"] is not None:
            diagnostics["trace"]["model_response"] = diagnostics["model_response"]
        message, status = segmentation_error(exc)
        logger.warning("切片失败 stage=%s status=%s error=%s trace=%s",
                       diagnostics["stage"], status, sanitize(message), sanitize(diagnostics["trace"]))
        raise
    logger.info("切片成功 elapsed_ms=%.1f segment_count=%s model_elapsed_ms=%s",
                (perf_counter() - started) * 1000, len(result.get("segments", [])),
                diagnostics["trace"].get("model_elapsed_ms", {}))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("切片详情 trace=%s", sanitize(diagnostics["trace"]))
    return result


def _segment(payload: dict, *, config: ClientSettings | None, diagnostics: dict) -> dict:
    """用正确文案和 ASR 词级时间生成片段；不调用 TTS/ASR，不降级模型失败。

    请求为 {script, asr_result, max_segment_chars}，读取 fun-asr transcripts 第一音轨，词时间为 begin_time/end_time 毫秒。
    替换/增删代价均为 1；波前搜索保留最远位置，平局依次优先替换、文案多字、
    ASR 多字。CT-Punc 对去标点的完整文案恢复标点，原标点优先叠加。
    按合并后的句读标点切开，保护数字/缩写并保留其他标点（空白本身不切）；超过 max_segment_chars（默认 8，不计标点空白）
    的片段完整保留并告警，不再请求大模型二次切分。模型只按最终片段提关键词。
    显式 config 仅用于当前调用；省略时读取 server/.env 与 IMV_ 环境变量，SDK 在返回前关闭。
    返回 segments（整型 segment_id、秒制 start_time/end_time、group_id、字符串 keyword、level）、
    warnings 和 trace；正文与原标点保留，补入预测标点，字幕去标点由下游合成处理。
    有 ASR 时 title 与片段共用关键词调用；无 ASR 时仅提标题词、不纠错；临时请求错误最多重试 MODEL_MAX_RETRIES 次。
    空内容或输出时间错误抛 ValueError，配置或模型输出错误抛
    RuntimeError，内部约束错误抛 AssertionError；ASR 嵌套读取和 SDK 异常原样传播。
    diagnostics 供共用入口记录已完成阶段的详细 trace；返回值只保留原有统计字段。
    """
    # 预算从进入切片开始计，读到服务端配置后再确定截止时间。
    entered = perf_counter()
    trace = diagnostics["trace"]
    logger = logging.getLogger("uvicorn.error")
    title = payload.get("title")
    segment_max_length = payload.get("max_segment_chars", 8)
    if type(segment_max_length) is not int or segment_max_length < 1:
        raise ValueError("max_segment_chars 必须是大于零的整数。")
    title_only = payload.get("asr_result") is None and title is not None
    if not title_only:
        # HTTP 类型校验由路由负责；有 ASR 时仍须有效文案，标点不参与对齐。
        script = payload.get("script") or ""
        chars = [
            (i, unicodedata.normalize("NFKC", c).lower())
            for i, c in enumerate(script)
            if not c.isspace() and not _is_punctuation(c)
        ]
        if not chars:
            raise ValueError("文案缺少有效字符。")
        if payload.get("asr_result") is None:
            raise ValueError("缺少 ASR 结果或标题。")
    # 客户端完整参数覆盖模型字段；服务端 HTTP 策略与切片超时仍从环境读取，不修改共享状态。
    diagnostics["stage"] = "config"
    config = _settings(config)
    deadline = entered + config.segmentation_budget_seconds
    base_url, key, model = config.llm_base_url, config.llm_api_key, config.llm_model

    def request_timeout() -> float:
        """所有切片模型请求共用剩余预算，并保留客户端更短的超时。"""
        # shortcut: 同步 HTTP 只在调用前后检查预算，需严格截止时改用可取消的异步请求。
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise TimeoutError("切片处理超时。")
        return min(config.llm_timeout_seconds, config.segmentation_model_timeout_seconds, remaining)

    def request_model(client, **kwargs):
        """临时模型请求错误额外重试，重算剩余预算；耗尽或不可重试时保留原异常。"""
        for attempt in range(MODEL_MAX_RETRIES + 1):
            timeout = request_timeout()
            try:
                return client.chat.completions.create(timeout=timeout, **kwargs)
            except (APIConnectionError, APIStatusError) as exc:
                if isinstance(exc, APIStatusError) and exc.status_code not in (408, 409, 429) and exc.status_code < 500:
                    raise
                if attempt == MODEL_MAX_RETRIES:
                    raise
                request_timeout()
                logger.warning("切片模型请求重试 stage=%s retry=%s error=%s",
                               diagnostics["stage"], attempt + 1, type(exc).__name__)

    if title_only:
        diagnostics["stage"] = "keywords"
        with OpenAI(base_url=base_url, api_key=key, timeout=request_timeout(), max_retries=0) as client:
            response = request_model(
                client,
                model=model, temperature=0.2, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": '从标题中提取一个关键词，只返回 JSON：{"title_keyword":"关键词"}，无合适词时返回空字符串。'},
                          {"role": "user", "content": title}],
                **_thinking(config),
            )
        raw = _response_content(response)
        diagnostics["model_response"] = raw
        logger.info("切片模型调用 stage=title_keywords attempt=1")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("切片模型输出 stage=title_keywords attempt=1 content=%r", sanitize(raw))
        request_timeout()
        try:
            keyword = json.loads(raw).get("title_keyword", "")
            return {"title_keyword": keyword if isinstance(keyword, str) else ""}
        except (AttributeError, TypeError, ValueError):
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
            content = [c for c in word["text"] if not c.isspace() and not _is_punctuation(c)]
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

    keyword_max_length = 12
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
    repair_block_count = 0
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
        repair_block_count += 1
    trace["repair_block_count"] = repair_block_count

    # 先去标点恢复，再按原字符间隙叠加原标点；随后独立判断切点。
    # 只按计数位置取 CT-Punc 的标点，正文始终用原文，模型改写英文大小写等不影响结果。
    offsets = [c[0] for c in chars]
    diagnostics["stage"] = "punctuation"
    request_timeout()
    started = perf_counter()
    restored = punctuation.restore("".join(c for c in script if not _is_punctuation(c)), deadline=deadline)
    trace["model_elapsed_ms"] = {"punctuation": round((perf_counter() - started) * 1000, 1)}
    request_timeout()
    additions, position = {}, 0
    for char in restored:
        if not _is_punctuation(char) and not char.isspace():
            position += 1
        elif _is_punctuation(char):
            begin = offsets[position - 1] + 1 if position else 0
            end = offsets[position] if position < len(offsets) else len(script)
            if not any(_is_punctuation(c) for c in script[begin:end]):
                additions[begin] = additions.get(begin, "") + char
    script = additions.get(0, "") + "".join(char + additions.get(i + 1, "") for i, char in enumerate(script))
    offsets = [i for i, c in enumerate(script) if not c.isspace() and not _is_punctuation(c)]
    diagnostics["stage"] = "punctuation_split"
    boundaries = _slice_boundaries(script, offsets)
    edges = list(boundaries)
    spans = list(zip(edges, edges[1:]))
    trace.update(punctuation_piece_count=len(spans), keyword_candidates={})
    remaining = [script[offsets[a]:offsets[b - 1] + 1] for a, b in spans if b - a > segment_max_length]
    trace["over_limit_pieces"] = remaining
    if remaining:
        warnings.append({"code": "segment_over_max_chars",
                         "message": f"{len(remaining)} 个片段超过每行 {segment_max_length} 字，已按标点完整保留。"})

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
    segments, rejected = [], 0
    with OpenAI(base_url=base_url, api_key=key, timeout=request_timeout(), max_retries=0) as client:
        for index, (a, b) in enumerate(spans, 1):
            begin, end = boundaries[a], boundaries[b]
            segments.append({
                "segment_id": index, "text": script[begin:end],
                "start_time_ms": round(starts[a]), "end_time_ms": round(ends[b - 1]), "keyword": "", "level": 1,
            })
        trace["segment_texts"] = [item["text"] for item in segments]

        # 按最终片段提关键词，与标题共用一次调用；纠错时回填原回复与错误。
        diagnostics["stage"] = "keywords"
        diagnostics["model_response"] = None
        prompt = (
            '为短视频素材召回提取关键词，只返回 JSON 对象。'
            "全篇关键词总数不限，逐段判断是否有值得检索画面的具体对象，无合适对象时省略该编号，不凑词。"
            f"每段最多1个词，每词最多{keyword_max_length}字；"
            "优先选择本段核心的具体名词或名词短语，如产品、动物、人物、食材、部位、工具、场景。"
            "保留有助于区分画面的必要修饰语，例如原文连续出现的‘散养的土鸡’；‘蛋清’‘蛋黄’也适合召回。"
            "不要只提取‘散养’‘新鲜’‘饱满’等动作或属性，也不要选‘囤一点’等营销引导。"
            "同一对象在不同片段中可重复选择，不为全篇去重而遗漏该段核心对象。"
            "keywords 为片段编号到关键词的对象，键必须是输入 segment_id 的十进制字符串，值为单个关键词字符串。"
            "只返回有关键词的编号，无关键词的片段可省略；不能改编号或把其他片段的词填到该编号。"
            "逐项确认词在该段内连续出现，保留大小写和全半角，不改写、跨标点拼接或借用其他段的词。"
            "自检编号、词长和原文匹配。只输出纯JSON，无Markdown或解释。"
        )
        content = [{"segment_id": s["segment_id"], "text": s["text"]} for s in segments]
        keyword_ids = {str(s["segment_id"]) for s in segments}
        example = {"keywords": {"1": "原文关键词"}}
        if title is not None:
            content = {"title": title, "segments": content}
            example["title_keyword"] = "标题关键词"
            prompt += (
                '本次输入为含 title 和 segments 的对象，keywords 只对应 segments。'
                '另从 title 按上述提词规则选一个关键词，返回同级 title_keyword 字符串，'
                '必须在标题中连续出现；标题为空或无合适关键词时返回空字符串。'
            )
        prompt += 'JSON 格式示例（全部无关键词时 keywords 返回空对象）：' + json.dumps(example, ensure_ascii=False)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
        ]
        started = perf_counter()
        try:
            for attempt in range(KEYWORD_MAX_ATTEMPTS):
                response = request_model(
                    client,
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    **_thinking(config),
                )
                raw = _response_content(response)
                diagnostics["model_response"] = raw
                logger.info("切片模型调用 stage=keywords attempt=%s", attempt + 1)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("切片模型输出 stage=keywords attempt=%s content=%r", attempt + 1, sanitize(raw))
                diagnostics["keyword_response"] = {"expected_segment_count": len(segments), "response": raw}
                request_timeout()
                try:
                    output = _json_output(response)
                    if title is not None:
                        title_keyword = _check_title_keyword(
                            output.get("title_keyword"), title, keyword_max_length, "title_keyword")
                    keywords = output.get("keywords")
                    diagnostics["keyword_response"].update(
                        keywords_type=type(keywords).__name__,
                        keyword_group_count=len(keywords) if isinstance(keywords, (dict, list)) else None,
                    )
                    if (
                        not isinstance(keywords, dict)
                        or any(key not in keyword_ids or not isinstance(word, str) for key, word in keywords.items())
                    ):
                        raise RuntimeError(
                            f"模型 keywords 必须为片段编号到字符串的对象，编号须为 1～{len(segments)} 的十进制字符串，"
                            "不能带前导零；无关键词可省略该编号。")
                    break
                except RuntimeError as exc:
                    if attempt == KEYWORD_MAX_ATTEMPTS - 1:
                        raise
                    # 部分兼容端点拒绝空 assistant 内容，空回复只追加纠错说明。
                    previous = [{"role": "assistant", "content": raw}] if isinstance(raw, str) and raw.strip() else []
                    messages = [*messages, *previous,
                                {"role": "user", "content": f"上次结果校验失败：{exc} 请修正并重新返回完整 JSON 对象。"}]
                    logger.warning("切片结果纠错 stage=keywords retry=%s error=%s", attempt + 1, sanitize(str(exc)))
        finally:
            trace["model_elapsed_ms"]["keywords"] = round((perf_counter() - started) * 1000, 1)
    trace["keyword_candidates"] = keywords
    for item in segments:
        key = str(item["segment_id"])
        candidates = [keywords[key]] if key in keywords else []
        item["keyword"] = _pick_keyword(item["text"], candidates, keyword_max_length)
        rejected += len(candidates) - bool(item["keyword"])
        # 有关键词即重点句 2，否则为普通句 1；CTA 需语义判断，不标注。
        item["level"] = 2 if item["keyword"] else 1

    # 按最终片段计算 ASR 句内分组。
    span_groups = [attribution[a] for a, _ in spans]
    group_totals, group_seen = Counter(span_groups), Counter()
    for item, group in zip(segments, span_groups):
        group_seen[group] += 1
        item["group_id"] = [group_seen[group], group_totals[group]]
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
    request_timeout()
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
            **_thinking(config),
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
