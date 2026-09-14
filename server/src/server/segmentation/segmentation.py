"""文案切片：单函数完成字符对齐、时间投射、模型规划和结果校验。"""

import bisect
from collections import Counter
import json
import math
import re
import unicodedata
from urllib.parse import urlparse

from pydantic import ValidationError
from openai import OpenAI

from .settings import Settings


def segment(payload: dict) -> dict:
    """用正确文案和 ASR 词级时间生成片段；不调用 TTS/ASR，不降级模型失败。

    请求为 {script, asr_result}，ASR 仅接受单音轨 fun-asr transcripts，词时间为 begin_time/end_time 毫秒。
    替换/增删代价均为 1；波前搜索保留最远位置，平局依次优先替换、文案多字、
    ASR 多字。模型只返回分句切点和关键词，时间投射和关键词校验由代码完成。
    配置来自当前目录 .env 及优先级更高的 IMV_ 环境变量；SDK 连接在返回前关闭。
    返回 segments（整型 segment_id、秒制 start_time/end_time、group_id、字符串 keyword、level）、
    warnings 和 trace；输入或预算错误抛 ValueError，配置或模型输出错误抛
    RuntimeError，内部约束错误抛 AssertionError，SDK 异常原样传播。
    """
    # 输入有界且不修改调用方数据；标点不参与对齐，原始下标仍用于完整切片。
    if not isinstance(payload, dict):
        raise ValueError("请求必须是包含 script 和 asr_result 的字典。")
    script = payload.get("script")
    if set(payload) != {"script", "asr_result"} or not isinstance(script, str) or not 1 <= len(script) <= 20000:
        raise ValueError("请求须包含 1～20000 字的 script 和 asr_result。")
    punctuation = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.!?;:\"'()<>[]{}~`")
    chars = [
        (i, unicodedata.normalize("NFKC", c).lower())
        for i, c in enumerate(script)
        if not c.isspace() and c not in punctuation
    ]
    if len(chars) < 2:
        raise ValueError("文案有效内容过短。")
    asr = payload["asr_result"]
    transcripts = asr.get("transcripts") if isinstance(asr, dict) else None
    if not isinstance(transcripts, list) or len(transcripts) != 1:
        raise ValueError("ASR 必须提供仅含一个音轨的 transcripts 数组。")
    transcript = transcripts[0]
    sentences = transcript.get("sentences") if isinstance(transcript, dict) else None
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= 20000:
        raise ValueError("ASR 需要有界的 sentences 词级时间轴。")
    # timeline_sentences 与 timeline 下标一一对应，记录每个字符所属的 ASR 句序号。
    timeline, timeline_sentences, word_count, text_count = [], [], 0, 0
    previous_end = 0
    for sentence_index, sentence in enumerate(sentences):
        words = sentence.get("words") if isinstance(sentence, dict) else None
        if not isinstance(words, list):
            raise ValueError("ASR 缺少 words 词级时间轴。")
        word_count += len(words)
        if word_count > 20000:
            raise ValueError("ASR 词数超出 20000 上限。")
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise ValueError("ASR 词必须包含 text 字符串。")
            text_count += len(word["text"])
            if text_count > 20000:
                raise ValueError("ASR 文本超出 20000 字符上限。")
            begin = word.get("begin_time")
            end = word.get("end_time")
            if (
                any(type(t) not in (int, float) or not 0 <= t <= 1e12 for t in (begin, end))
                or not 0 <= previous_end <= begin < end
            ):
                raise ValueError("ASR 时间必须有限、非负、单调且不重叠。")
            previous_end = end
            # ponytail: 词内字符均分词时长，并非真实字级强制对齐；精度不足时需上游提供更细时间轴。
            content = [c for c in word["text"] if not c.isspace() and c not in punctuation]
            # 空内容不进入循环或执行除法；该词的时间仍参与上面的单调性校验。
            for i, char in enumerate(content):
                timeline.append(
                    (
                        unicodedata.normalize("NFKC", char).lower(),
                        begin + (end - begin) * i / len(content),
                        begin + (end - begin) * (i + 1) / len(content),
                        i == 0,
                    )
                )
                timeline_sentences.append(sentence_index)
    if not timeline:
        raise ValueError("ASR 缺少有效发音字符。")
    if (Counter(c[1] for c in chars) - Counter(c[0] for c in timeline)).total() > len(chars) // 2:
        raise ValueError("文案与 ASR 差异过大。")

    # 每次调用自动读取配置；配置错误仍归为模型错误，不向 HTTP 暴露配置值。
    try:
        config = Settings()
    except ValidationError:
        raise RuntimeError("模型或切片配置缺失或不合法，请检查 IMV_ 配置。") from None
    minimum, maximum = config.segment_min_duration_ms, config.segment_max_duration_ms
    # 先剥离相同前后缀；比较、候选状态与单侧字符都计入同一工作预算。
    budget = config.segment_max_alignment_work
    prefix = suffix = 0
    limit = min(len(chars), len(timeline))
    for backwards in (False, True):
        while prefix + suffix < limit:
            budget -= 1
            if budget < 0:
                raise ValueError("对齐工作预算已耗尽。")
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
        budget -= rows + columns
        if budget < 0:
            raise ValueError("对齐工作预算已耗尽。")
        middle = [("script_extra", prefix + i, None) for i in range(rows)]
        middle += [("asr_extra", None, prefix + j) for j in range(columns)]
    else:
        # ponytail: O(D²) 回溯状态受预算约束；大差异成为常态时再改线性空间回溯。
        history, previous, reached = [], {}, False
        for distance in range(max(rows, columns) + 1):
            current = {}
            for diagonal in range(max(-distance, -columns), min(distance, rows) + 1):
                budget -= 1
                if budget < 0:
                    raise ValueError("对齐工作预算已耗尽。")
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
                    budget -= 1
                    if budget < 0:
                        raise ValueError("对齐工作预算已耗尽。")
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
    # 分母覆盖两侧文本，避免 ASR 大量多字仍被视为文案完全匹配。
    ratio = counts["match"] / max(len(chars), len(timeline))
    if ratio < 0.5:
        raise ValueError("文案与 ASR 差异过大。")
    warnings = []
    if ratio < 0.9:
        warnings.append({"code": "low_alignment_match_ratio", "message": "文案与 ASR 存在较多差异。"})

    # 替换直接继承时间；增删连续段向两侧扩一字，合并后仅在块内均分时间。
    starts, ends, word_starts = [0.0] * len(chars), [0.0] * len(chars), [False] * len(chars)
    blocks, run = [], None
    for position, (kind, i, j) in enumerate([*ops, ("match", None, None)]):
        if i is not None and j is not None:
            starts[i], ends[i], word_starts[i] = timeline[j][1:]
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

    # 仅保护原文连续的英文、数字串（含小数、连字符和百分号），不跨空格或中文标点保护。
    offsets = [c[0] for c in chars]
    forbidden = set()
    for token in re.finditer(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*%?", script):
        begin, end = bisect.bisect_left(offsets, token.start()), bisect.bisect_left(offsets, token.end())
        forbidden.update(range(begin + 1, end))
    # 增删修复块内时间为估算值，保留整块以免制造看似精确的切点。
    for begin, end in repair_ranges:
        forbidden.update(range(begin + 1, end))
    legal = sorted(set(range(1, len(chars))) - forbidden)
    # ponytail: MVP 仅向模型提供中文标点分句；无此类标点时仅靠后续时长拆分，需多语言时再扩展候选。
    clauses = list(re.finditer(r"[^，。！？；：、…]*[，。！？；：、…]+|[^，。！？；：、…]+$", script))
    listing = [{"id": i + 1, "text": m.group()} for i, m in enumerate(clauses)]
    base_url, key, model = config.llm_base_url, config.llm_api_key, config.llm_model
    try:
        address = urlparse(base_url)
        address.port  # 验证可选端口，非法地址不进入 SDK。
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
    segments, merge_count, split_count, rejected = [], 0, 0, 0
    # 两次调用有先后依赖：语义切点经时长调整后，再让模型标注最终片段；重试仅由 SDK 负责。
    with OpenAI(base_url=base_url, api_key=key, timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
        for stage in ("boundaries", "keywords"):
            if stage == "boundaries":
                prompt = (
                    '将口播文案切成短句画面，只返回 JSON：{"boundaries_after":[1,3]}。'
                    "数组须列全所有选中的分句编号，升序、不重复，从1开始且不含最后一句；不是只选几个代表性切点。"
                    "逐个检查相邻分句，默认切开独立信息点；仅语法不完整、必须依赖相邻句且合并后不超过10字时才合并。"
                    "以6～8字为节奏参考，3～5字可独立强调；已有超长分句保留前后边界，不再合并。"
                    "同一话题、产品或连续卖点仍分别切开；只用已有分句边界，时长交给程序处理。"
                    "以输入原文为准，不改写、不删字、不生成时间；只输出纯JSON，无Markdown或解释。"
                )
                content = listing
            else:
                prompt = (
                    '从全篇挑选3～4个最核心关键词，只返回 JSON：{"keywords":[[],["词"],[]]}。'
                    "所有内层数组的词数总和不得超过4，不是每段3～4个；不足可少选，不凑数。"
                    f"每段最多{min(1, config.segment_max_keywords)}个词，每词最多{config.segment_keyword_max_length}字；"
                    "先全篇筛选痛点、优势、收益或行动引导，再将每词放入一个对应片段，其余留空。"
                    "输出数组长度必须等于输入片段数，第i项只对应第i段，空数组不能省略。"
                    "逐项确认词在该段内连续出现，保留大小写和全半角，不改写或借用其他段的词。"
                    "提交前数一遍全篇词数，超过4就删除次要词；只输出纯JSON，无Markdown或解释。"
                )
                content = [s["text"] for s in segments]
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
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
            if stage == "boundaries":
                ids = output.get("boundaries_after")
                if not isinstance(ids, list) or any(type(n) is not int or not 1 <= n < len(clauses) for n in ids):
                    raise RuntimeError("模型 boundaries_after 必须为有效分句编号数组，不含最后一句。")
                cuts = set()
                for number in ids:
                    cut = bisect.bisect_left(offsets, clauses[number - 1].end())
                    if 0 < cut < len(chars) and legal:
                        cut = min(legal, key=lambda i: (abs(i - cut), i))
                        cuts.add(cut)
                edges = [0, *sorted(cuts), len(chars)]
                spans = list(zip(edges, edges[1:]))
                # 合并最短片段，再逐段均衡拆分；每次操作都减少待处理区间或字数。
                # ponytail: 短片段合并使用线性扫描；接近两万切点时再改优先队列。
                while len(spans) > 1:
                    short = min(
                        (i for i, (a, b) in enumerate(spans) if ends[b - 1] - starts[a] < minimum),
                        key=lambda i: ends[spans[i][1] - 1] - starts[spans[i][0]],
                        default=None,
                    )
                    if short is None:
                        break
                    options = []
                    for first in (short - 1, short):
                        if 0 <= first < len(spans) - 1:
                            duration = ends[spans[first + 1][1] - 1] - starts[spans[first][0]]
                            options.append(((duration > maximum, abs(duration - (minimum + maximum) / 2)), first))
                    first = min(options)[1]
                    spans[first : first + 2] = [(spans[first][0], spans[first + 1][1])]
                    merge_count += 1
                pending, spans = list(reversed(spans)), []
                while pending:
                    a, b = pending.pop()
                    duration = ends[b - 1] - starts[a]
                    candidates = legal[bisect.bisect_right(legal, a) : bisect.bisect_left(legal, b)]
                    if duration <= maximum or not candidates:
                        spans.append((a, b))
                        continue
                    target = duration / math.ceil(duration / maximum)
                    cut = min(
                        candidates,
                        key=lambda i: (
                            min(ends[i - 1] - starts[a], ends[b - 1] - starts[i]) < minimum,
                            not word_starts[i],
                            starts[i] - ends[i - 1] < 120,
                            abs(ends[i - 1] - starts[a] - target),
                            i,
                        ),
                    )
                    pending.extend([(cut, b), (a, cut)])
                    split_count += 1
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
                group_totals, group_seen = Counter(span_groups), Counter()
                for index, (a, b) in enumerate(spans, 1):
                    begin = 0 if a == 0 else offsets[a]
                    end = len(script) if b == len(chars) else offsets[b]
                    group = span_groups[index - 1]
                    group_seen[group] += 1
                    segments.append(
                        {
                            "segment_id": index,
                            "group_id": [group_seen[group], group_totals[group]],
                            "text": script[begin:end],
                            "start_time_ms": round(starts[a]),
                            "end_time_ms": round(ends[b - 1]),
                            "keyword": "",
                            "level": 1,
                        }
                    )
            else:
                groups = output.get("keywords")
                if (
                    not isinstance(groups, list)
                    or len(groups) != len(segments)
                    or any(not isinstance(g, list) or any(not isinstance(w, str) for w in g) for g in groups)
                ):
                    raise RuntimeError("模型关键词数组必须与片段一一对应且元素为字符串。")
                # 输出字段是单个字符串，因此每段只保留原文中最靠前的一个有效词；
                # 配置为 0 时不选词，被丢弃的其他候选同样计入 keyword_rejected_count。
                limit = min(1, config.segment_max_keywords)
                # 只校验逐字存在、去重和长度/数量；包含关系不代表无效，长短词都可能是有效选择。
                for item, candidates in zip(segments, groups):
                    accepted = {}
                    for candidate in candidates:
                        word = candidate.strip()
                        start = item["text"].find(word)
                        if not word or word in accepted or len(word) > config.segment_keyword_max_length or start < 0:
                            rejected += 1
                        else:
                            accepted[word] = start
                    keywords = sorted(accepted, key=accepted.get)
                    rejected += len(keywords) - min(len(keywords), limit)
                    item["keyword"] = keywords[0] if limit and keywords else ""
                # level 只由代码判定：有关键词即重点句 2，其余保持普通句 1；CTA 需语义判断，不标注。
                for item in segments:
                    if item["keyword"]:
                        item["level"] = 2

    # 吸收允许范围内的停顿，再检查输出硬约束；时长软约束只产生告警。
    for previous, current in zip(segments, segments[1:]):
        if previous["end_time_ms"] < current["start_time_ms"]:
            if current["start_time_ms"] - previous["start_time_ms"] <= maximum:
                previous["end_time_ms"] = current["start_time_ms"]
            else:
                warnings.append({"code": "segment_gap_preserved", "message": "保留较长停顿。"})
    if "".join(s["text"] for s in segments) != script:
        raise AssertionError("片段未完整覆盖文案。")
    previous_end = 0
    for item in segments:
        if not previous_end <= item["start_time_ms"] < item["end_time_ms"]:
            raise ValueError("ASR 时间精度不足，无法生成合法且不重叠的片段。")
        previous_end = item["end_time_ms"]
        if not minimum <= item["end_time_ms"] - item["start_time_ms"] <= maximum:
            warnings.append(
                {
                    "code": "segment_duration_out_of_range",
                    "message": "该片段无法满足时长约束。",
                    "detail": {"segment_id": item["segment_id"]},
                }
            )
    # 输出约定使用秒制起止时间；上面的时长约束、停顿吸收和告警判断仍以毫秒为准。
    for item in segments:
        item["start_time"] = item.pop("start_time_ms") / 1000
        item["end_time"] = item.pop("end_time_ms") / 1000
    return {
        "segments": segments,
        "warnings": warnings,
        "trace": {
            "matched_chars": counts["match"],
            "substitution_chars": counts["substitution"],
            "script_extra_chars": counts["script_extra"],
            "asr_extra_chars": counts["asr_extra"],
            "edit_cost": len(ops) - counts["match"],
            "repair_block_count": len(repair_ranges),
            "merge_count": merge_count,
            "split_count": split_count,
            "segment_count": len(segments),
            "keyword_rejected_count": rejected,
        },
    }
