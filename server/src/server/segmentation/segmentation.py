"""文案切片：单函数完成字符对齐、时间投射、模型规划和结果校验。"""

import bisect
from collections import Counter
import json
import re
import unicodedata
from urllib.parse import urlparse

from pydantic import ValidationError
from openai import OpenAI

from .settings import Settings


def segment(payload: dict) -> dict:
    """用正确文案和 ASR 词级时间生成片段；不调用 TTS/ASR，不降级模型失败。

    请求为 {script, asr_result}，读取 fun-asr transcripts 第一音轨，词时间为 begin_time/end_time 毫秒。
    替换/增删代价均为 1；波前搜索保留最远位置，平局依次优先替换、文案多字、
    ASR 多字。模型只返回分句切点和关键词，时间投射和关键词校验由代码完成。
    配置来自固定的 server/.env 及优先级更高的 IMV_ 环境变量；SDK 连接在返回前关闭。
    返回 segments（整型 segment_id、秒制 start_time/end_time、group_id、字符串 keyword、level）、
    warnings 和 trace；空内容或输出时间错误抛 ValueError，配置或模型输出错误抛
    RuntimeError，内部约束错误抛 AssertionError；ASR 嵌套读取和 SDK 异常原样传播。
    """
    # 直接调用须提供约定字段；HTTP 类型校验由路由负责。标点不参与对齐，保留原始下标。
    script = payload["script"]
    punctuation = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.!?;:\"'()<>[]{}~`")
    chars = [
        (i, unicodedata.normalize("NFKC", c).lower())
        for i, c in enumerate(script)
        if not c.isspace() and c not in punctuation
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
            content = [c for c in word["text"] if not c.isspace() and c not in punctuation]
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

    # 每次调用自动读取配置；配置错误仍归为模型错误，不向 HTTP 暴露配置值。
    try:
        config = Settings()
    except ValidationError:
        raise RuntimeError("模型或切片配置缺失或不合法，请检查 IMV_ 配置。") from None
    # 固定业务规则：候选片段至少 2 秒，每段最多一个关键词，词长最多 12 字。
    minimum, keyword_max_length = 2000, 12
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

    # 仅保护原文连续的英文、数字串（含小数、连字符和百分号），不跨空格或中文标点保护。
    offsets = [c[0] for c in chars]
    forbidden = set()
    for token in re.finditer(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*%?", script):
        begin, end = bisect.bisect_left(offsets, token.start()), bisect.bisect_left(offsets, token.end())
        forbidden.update(range(begin + 1, end))
    # 增删修复块内时间为估算值，保留整块以免制造看似精确的切点。
    for begin, end in repair_ranges:
        forbidden.update(range(begin + 1, end))
    # ponytail: 只使用中文标点候选；无候选或全文不足 2 秒时保持整段，需多语言时再扩展。
    # 从左到右保留切点，两侧均留足 2 秒；过滤只隐藏边界，不删除原文。
    candidate_edges = [0]
    for clause in re.finditer(r"[^，。！？；：、…]*[，。！？；：、…]+|[^，。！？；：、…]+$", script):
        cut = bisect.bisect_left(offsets, clause.end())
        if (
            0 < cut < len(chars) and cut not in forbidden
            and ends[cut - 1] - starts[candidate_edges[-1]] >= minimum
            and ends[-1] - starts[cut] >= minimum
        ):
            candidate_edges.append(cut)
    candidate_edges.append(len(chars))
    text_edges = [0, *[offsets[i] for i in candidate_edges[1:-1]], len(script)]
    listing = [
        {"id": i, "text": script[a:b]}
        for i, (a, b) in enumerate(zip(text_edges, text_edges[1:]), 1)
    ]
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
    # 两次调用有先后依赖：模型选择候选切点后，再标注最终片段；重试仅由 SDK 负责。
    with OpenAI(base_url=base_url, api_key=key, timeout=config.llm_timeout_seconds, max_retries=config.llm_max_retries) as client:
        for stage in ("boundaries", "keywords"):
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
            else:
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
                if not isinstance(ids, list) or any(type(n) is not int or not 1 <= n < len(listing) for n in ids):
                    raise RuntimeError("模型 boundaries_after 必须为有效分句编号数组，不含最后一句。")
                edges = [0, *[candidate_edges[n] for n in sorted(set(ids))], len(chars)]
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

    # 保留原始停顿，检查文本覆盖和输出时间。
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
        "segments": segments,
        "warnings": warnings,
        "trace": {
            "matched_chars": counts["match"],
            "substitution_chars": counts["substitution"],
            "script_extra_chars": counts["script_extra"],
            "asr_extra_chars": counts["asr_extra"],
            "edit_cost": len(ops) - counts["match"],
            "repair_block_count": len(repair_ranges),
            "segment_count": len(segments),
            "keyword_rejected_count": rejected,
        },
    }
