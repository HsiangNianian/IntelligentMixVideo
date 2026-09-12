"""文案切片：单函数完成字符对齐、时间投射、模型规划和结果校验。"""

import bisect
from collections import Counter
import json
import math
import os
import re
import unicodedata
from urllib.parse import urlparse

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from openai import APIError, APITimeoutError, OpenAI

router = APIRouter()


@router.post("/segmentations", response_model=None)
def segment(payload: dict) -> dict | JSONResponse:
    """用正确文案和 ASR 词级时间生成片段；不调用 TTS/ASR，不降级模型失败。

    请求为 {script, asr_result}，ASR 支持 sentences 或 fun-asr transcripts[0]。
    替换/增删代价均为 1；波前搜索保留最远位置，平局依次优先替换、文案多字、
    ASR 多字。模型只返回分句切点和关键词，所有时间和下标由代码计算。
    配置来自当前目录 .env 及优先级更高的 IMV_ 环境变量；SDK 连接在返回前关闭。
    正常返回 segments/warnings/trace；无效输入、超预算、模型异常返回明确错误。
    """
    try:
        # 输入有界且不修改调用方数据；标点不参与对齐，原始下标仍用于完整切片。
        script = payload.get("script")
        if set(payload) != {"script", "asr_result"} or not isinstance(script, str) or not 1 <= len(script) <= 20000:
            raise HTTPException(422, "请求须包含 1～20000 字的 script 和 asr_result。")
        punctuation = set("，。！？、；：“”‘’（）《》〈〉【】〔〕…—～·,.!?;:\"'()<>[]{}~`")
        chars = [
            (i, unicodedata.normalize("NFKC", c).lower(), c)
            for i, c in enumerate(script)
            if not c.isspace() and c not in punctuation
        ]
        if len(chars) < 2:
            raise HTTPException(422, "文案有效内容过短。")
        asr = payload["asr_result"]
        if isinstance(asr, dict) and "sentences" not in asr:
            transcripts = asr.get("transcripts")
            asr = transcripts[0] if isinstance(transcripts, list) and transcripts else None
        sentences = asr.get("sentences") if isinstance(asr, dict) else None
        if not isinstance(sentences, list) or not 1 <= len(sentences) <= 20000:
            raise HTTPException(422, "ASR 需要有界的 sentences 词级时间轴。")
        timeline, word_count, text_count = [], 0, 0
        previous_end = 0
        for sentence in sentences:
            words = sentence.get("words") if isinstance(sentence, dict) else None
            if not isinstance(words, list):
                raise HTTPException(422, "ASR 缺少 words 词级时间轴。")
            word_count += len(words)
            if word_count > 20000:
                raise HTTPException(422, "ASR 词数超出 20000 上限。")
            for word in words:
                if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                    raise HTTPException(422, "ASR 词必须包含 text 字符串。")
                text_count += len(word["text"])
                if text_count > 20000:
                    raise HTTPException(422, "ASR 文本超出 20000 字符上限。")
                begin = word.get("begin_time_ms", word.get("begin_time"))
                end = word.get("end_time_ms", word.get("end_time"))
                if (
                    any(type(t) not in (int, float) or not 0 <= t <= 1e12 for t in (begin, end))
                    or not 0 <= previous_end <= begin < end
                ):
                    raise HTTPException(422, "ASR 时间必须有限、非负、单调且不重叠。")
                previous_end = end
                content = [c for c in word["text"] if not c.isspace() and c not in punctuation]
                for i, char in enumerate(content):
                    timeline.append(
                        (
                            unicodedata.normalize("NFKC", char).lower(),
                            begin + (end - begin) * i / len(content),
                            begin + (end - begin) * (i + 1) / len(content),
                            i == 0,
                        )
                    )
        if not timeline:
            raise HTTPException(422, "ASR 缺少有效发音字符。")
        if (Counter(c[1] for c in chars) - Counter(c[0] for c in timeline)).total() > len(chars) // 2:
            raise HTTPException(422, "文案与 ASR 差异过大。")

        # 仅保留当前功能使用的配置；不新增配置类或缓存，避免请求间共享状态。
        config = {k.upper(): v for k, v in {**dotenv_values(".env"), **os.environ}.items()}
        defaults = {
            "MIN_DURATION_MS": 1200,
            "MAX_DURATION_MS": 6000,
            "MAX_KEYWORDS": 5,
            "KEYWORD_MAX_LENGTH": 12,
            "MAX_ALIGNMENT_WORK": 250000,
        }
        try:
            limits = {k: int(config.get("IMV_SEGMENT_" + k, v)) for k, v in defaults.items()}
            retries = int(config.get("IMV_LLM_MAX_RETRIES", 1))
            timeout = float(config.get("IMV_LLM_TIMEOUT_SECONDS", 120))
        except (ValueError, TypeError):
            raise HTTPException(502, "模型或切片配置必须使用合法数值。") from None
        minimum, maximum = limits["MIN_DURATION_MS"], limits["MAX_DURATION_MS"]
        if not (
            200 <= minimum < maximum <= 30000
            and 0 <= limits["MAX_KEYWORDS"] <= 20
            and 2 <= limits["KEYWORD_MAX_LENGTH"] <= 30
            and limits["MAX_ALIGNMENT_WORK"] > 0
            and 0 <= retries <= 3
            and math.isfinite(timeout)
            and timeout > 0
        ):
            raise HTTPException(502, "模型或切片配置超出允许范围。")
        budget = limits["MAX_ALIGNMENT_WORK"]
        prefix = suffix = 0
        limit = min(len(chars), len(timeline))
        for backwards in (False, True):
            while prefix + suffix < limit:
                budget -= 1
                if budget < 0:
                    raise HTTPException(422, "对齐工作预算已耗尽。")
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
                raise HTTPException(422, "对齐工作预算已耗尽。")
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
                        raise HTTPException(422, "对齐工作预算已耗尽。")
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
                            raise HTTPException(422, "对齐工作预算已耗尽。")
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
                raise HTTPException(500, "对齐未到达终点。")
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
        ratio = counts["match"] / len(chars)
        if ratio < 0.5:
            raise HTTPException(422, "文案与 ASR 差异过大。")
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
                raise HTTPException(422, "修复块缺少可继承的 ASR 时间。")
            begin_time, end_time = timeline[sources[0]][1], timeline[sources[-1]][2]
            step = (end_time - begin_time) / len(indices)
            for order, i in enumerate(indices):
                starts[i], ends[i] = begin_time + step * order, begin_time + step * (order + 1)
            repair_ranges.append((indices[0], indices[-1] + 1))

        # 可切位置禁止拆开英文、数字与紧随的单位，也避开局部估算时间。
        forbidden = set()
        for i in range(1, len(chars)):
            previous_char, following = chars[i - 1][2], chars[i][2]
            if (
                previous_char.isascii()
                and previous_char.isalnum()
                and (following.isascii() and following.isalnum() or following in "%.-")
            ):
                forbidden.add(i)
        for begin, end in repair_ranges:
            forbidden.update(range(begin + 1, end))
        legal = sorted(set(range(1, len(chars))) - forbidden)
        offsets = [c[0] for c in chars]
        clauses = list(re.finditer(r"[^，。！？；：、…]*[，。！？；：、…]+|[^，。！？；：、…]+$", script))
        listing = [{"id": i + 1, "text": m.group()} for i, m in enumerate(clauses)]
        base_url, key, model = (config.get("IMV_LLM_" + k) for k in ("BASE_URL", "API_KEY", "MODEL"))
        if not all(isinstance(v, str) and v.strip() for v in (base_url, key, model)):
            raise HTTPException(502, "请配置 IMV_LLM_BASE_URL、IMV_LLM_API_KEY 和 IMV_LLM_MODEL。")
        try:
            address = urlparse(base_url)
            address.port  # 验证可选端口，非法地址不进入 SDK。
        except ValueError:
            raise HTTPException(502, "模型地址格式不合法。") from None
        if (
            address.scheme not in ("https", "http")
            or not address.netloc
            or (
                address.scheme == "http"
                and address.hostname not in ("localhost", "127.0.0.1", "::1")
                and (config.get("IMV_ALLOW_INSECURE_LLM_HTTP") or "false").lower() != "true"
            )
        ):
            raise HTTPException(502, "模型地址必须有效，远程 HTTP 需要显式授权。")
        segments, merge_count, split_count, rejected = [], 0, 0, 0
        with OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=retries) as client:
            for stage in ("boundaries", "keywords"):
                if stage == "boundaries":
                    prompt = (
                        '按语义选择分句之后的画面切点，只返回 JSON：{"boundaries_after":[1,3]}。'
                        "编号从1开始，不含最后一句，不生成任何时间或新文本。"
                    )
                    content = listing
                else:
                    prompt = (
                        '为每段文案提取逐字存在的关键词，只返回 JSON：{"keywords":[["词"],[]]}。'
                        f"数组与片段一一对应，每段最多{limits['MAX_KEYWORDS']}个词，"
                        f"每词最多{limits['KEYWORD_MAX_LENGTH']}字，保留有意义的单字，避免虚词。"
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
                    raise HTTPException(502, "模型返回空内容。")
                raw = response.choices[0].message.content.strip()
                if raw.startswith("```json") and raw.endswith("```"):
                    raw = raw[7:-3].strip()
                try:
                    output = json.loads(raw)
                except json.JSONDecodeError:
                    raise HTTPException(502, "模型返回非法 JSON。") from None
                if not isinstance(output, dict):
                    raise HTTPException(502, "模型必须返回 JSON 对象。")
                if stage == "boundaries":
                    ids = output.get("boundaries_after")
                    if not isinstance(ids, list):
                        raise HTTPException(502, "模型缺少 boundaries_after 数组。")
                    cuts = set()
                    for number in ids:
                        if type(number) is not int or not 1 <= number < len(clauses):
                            continue
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
                    for index, (a, b) in enumerate(spans, 1):
                        begin = 0 if a == 0 else offsets[a]
                        end = len(script) if b == len(chars) else offsets[b]
                        segments.append(
                            {
                                "segment_id": f"seg_{index:03d}",
                                "text": script[begin:end],
                                "start_time_ms": round(starts[a]),
                                "end_time_ms": round(ends[b - 1]),
                                "keywords": [],
                            }
                        )
                else:
                    groups = output.get("keywords")
                    if (
                        not isinstance(groups, list)
                        or len(groups) != len(segments)
                        or any(not isinstance(g, list) or any(not isinstance(w, str) for w in g) for g in groups)
                    ):
                        raise HTTPException(502, "模型关键词数组必须与片段一一对应且元素为字符串。")
                    for item, candidates in zip(segments, groups):
                        accepted = {}
                        for candidate in candidates:
                            word = candidate.strip()
                            start = item["text"].find(word)
                            if not word or word in accepted or len(word) > limits["KEYWORD_MAX_LENGTH"] or start < 0:
                                rejected += 1
                            else:
                                accepted[word] = {"text": word, "start": start, "end": start + len(word)}
                        keywords = [
                            v for k, v in accepted.items() if not any(k != other and k in other for other in accepted)
                        ]
                        keywords.sort(key=lambda k: (k["start"], k["end"]))
                        rejected += len(accepted) - min(len(keywords), limits["MAX_KEYWORDS"])
                        item["keywords"] = keywords[: limits["MAX_KEYWORDS"]]

        # 吸收允许范围内的停顿，再检查输出硬约束；时长软约束只产生告警。
        for previous, current in zip(segments, segments[1:]):
            if previous["end_time_ms"] < current["start_time_ms"]:
                if current["start_time_ms"] - previous["start_time_ms"] <= maximum:
                    previous["end_time_ms"] = current["start_time_ms"]
                else:
                    warnings.append({"code": "segment_gap_preserved", "message": "保留较长停顿。"})
        if "".join(s["text"] for s in segments) != script:
            raise HTTPException(500, "片段未完整覆盖文案。")
        previous_end = 0
        for item in segments:
            if not previous_end <= item["start_time_ms"] < item["end_time_ms"]:
                raise HTTPException(422, "ASR 时间精度不足，无法生成合法且不重叠的片段。")
            previous_end = item["end_time_ms"]
            if not minimum <= item["end_time_ms"] - item["start_time_ms"] <= maximum:
                warnings.append(
                    {
                        "code": "segment_duration_out_of_range",
                        "message": "该片段无法满足时长约束。",
                        "detail": {"segment_id": item["segment_id"]},
                    }
                )
            for keyword in item["keywords"]:
                if item["text"][keyword["start"] : keyword["end"]] != keyword["text"]:
                    raise HTTPException(500, "关键词下标无法回溯原文。")
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
    except HTTPException as exc:
        return JSONResponse({"error": {"message": exc.detail}}, status_code=exc.status_code)
    except APITimeoutError:
        return JSONResponse({"error": {"message": "模型请求超时。"}}, status_code=504)
    except APIError:
        return JSONResponse({"error": {"message": "模型服务请求失败。"}}, status_code=502)
