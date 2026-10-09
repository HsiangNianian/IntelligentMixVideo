"""由业务快照生成 IMS Timeline；纯素材按序裁尾，无声标题铺满全片，其余对象沿用模板规则。"""

from math import floor
import re

from pydantic import TypeAdapter

from ..segmentation.segmentation import SUBTITLE_PUNCTUATION
from ..template.schema import CATEGORY_PARAMETERS, EffectTemplateEditor, EffectTrack, Template
from ..template.timing import resolve_track
from .schema import CompositionRequest, MatchedSegment, PositiveSeconds, Segment


def validate_segments(segments: list[Segment], duration_ms: int) -> None:
    """校验非空、唯一编号、单调秒制区间，尾部静音仍由音频总长决定。"""
    if not segments or type(duration_ms) is not int or duration_ms <= 0:
        raise ValueError("缺少有效切片或音频总时长")
    previous = 0
    ids = set()
    for item in segments:
        if item.segment_id in ids or not previous <= item.start_time < item.end_time <= duration_ms / 1000:
            raise ValueError("切片编号重复、时间重叠或超出音频范围")
        ids.add(item.segment_id)
        previous = item.end_time


def validate_matches(segments: list[Segment], matches: list[MatchedSegment]) -> None:
    """严格核对数量、编号、顺序和时间；文字仅忽略首尾空白，不改写本地切片或匹配回执。"""
    if len(segments) != len(matches):
        raise ValueError("匹配片段数量不一致")
    for source, matched in zip(segments, matches):
        if (
            matched.segment_id != source.segment_id or matched.text.strip() != source.text.strip()
            or matched.start_time != source.start_time
            or matched.end_time != source.end_time
        ):
            raise ValueError("匹配片段编号、文案或时间与切片不一致")


def format_subtitle_keyword(content: str, keyword: str, config: EffectTemplateEditor) -> str:
    """按模板选项为本段首次出现的关键词生成 IMS 局部样式指令。"""
    return format_keyword(content, keyword, config, "subtitle")


def first_title_keyword(content: str) -> str:
    """从请求标题首段连续文字取前两个字，供没有手动关键词的标题样式使用。"""
    match = re.search(r"[^\W_]{1,2}", content)
    return match.group() if match else ""


def format_keyword(content: str, keyword: str, config: EffectTemplateEditor, role: str) -> str:
    """为标题或字幕的首个匹配词语添加局部样式；标题缺少该词语时保留原文。"""
    color = getattr(config, f"{role}_keyword_color")
    size = getattr(config, f"{role}_keyword_size")
    styles = (
        (getattr(config, f"{role}_keyword_bold"), r"\b1", r"\b0"),
        (getattr(config, f"{role}_keyword_italic"), r"\i1", r"\i0"),
        (getattr(config, f"{role}_keyword_underline"), r"\u1", r"\u0"),
        (getattr(config, f"{role}_keyword_strikeout"), r"\s1", r"\s0"),
    )
    enabled = [(start, end) for selected, start, end in styles if selected]
    if not keyword or not (enabled or color or size):
        return content
    position = content.find(keyword)
    if position < 0:
        if role == "title":
            return content
        raise ValueError("关键词不在字幕文字中")
    bgr = (color[5:7] + color[3:5] + color[1:3]).upper() if color else ""
    opening = (rf"\1c&{bgr}&" if color else "") + (rf"\fs{size}" if size else "") + "".join(start for start, _ in enabled)
    closing = (r"\1c" if color else "") + (r"\fs" if size else "") + "".join(end for _, end in enabled)
    return f"{content[:position]}{{{opening}}}{keyword}{{{closing}}}{content[position + len(keyword):]}"


def build_timeline(
    request: dict, template: dict, segments: list[dict], matches: list[dict],
    duration_ms: int | float, *, width: int, height: int, fps: int,
    material_durations: list[float] | None = None,
) -> tuple[dict, list[str]]:
    """返回时间线及转场调整说明；无网络、模型、随机值或对输入快照的修改。"""
    request = CompositionRequest.model_validate(request)
    template = Template.model_validate(template)
    standard = request.composition_mode == "standard"
    has_subtitles = request.composition_mode != "materials_silent" and bool(request.text)
    source = TypeAdapter(list[Segment]).validate_python(segments) if has_subtitles else []
    matched = TypeAdapter(list[MatchedSegment]).validate_python(matches) if standard else []
    if has_subtitles:
        validate_segments(source, duration_ms)
    if standard:
        validate_matches(source, matched)
    if not all(type(v) is int and v > 0 for v in (width, height, fps)) or fps > 60:
        raise ValueError("输出尺寸或帧率无效")
    duration = TypeAdapter(PositiveSeconds).validate_python(duration_ms) / 1000
    by_id = {item.id: item for item in template.effects}
    if len(by_id) != len(template.effects):
        raise ValueError("模板效果快照包含重复引用")

    def parameters_for(config: EffectTemplateEditor) -> dict:
        """逐个核对对象的可信效果快照，即使当前没有可显示区间也检查引用。"""
        parameters = {}
        for key, effect_id in config.selected_effects().items():
            category = {
                "title_flower": "flower", "subtitle_flower": "flower", "bubble": "bubble",
                "filter": "filter", "vfx": "vfx/normal", "transition": "transition/normal",
            }.get(key, key.rsplit("_", 1)[-1])
            item = by_id.get(effect_id)
            if (
                item is None or item.category != category or item.id not in template.effect_ids
                or item.parameters != {CATEGORY_PARAMETERS[category]: item.effect_id}
                or item.effect_id == "random"
            ):
                raise ValueError("模板效果引用、类别或参数无效")
            parameters[key] = item.parameters
        return parameters

    def video(url: str, kind: str, start: float, end: float, source_start: float) -> dict:
        """数字人源时间等于成片时间；素材从源零点起，保留比例并模糊填充留白。"""
        clip = {
            "Type": "Image" if kind == "image" else "Video", "MediaURL": url,
            "TimelineIn": start, "TimelineOut": end,
            "Width": width, "Height": height, "AdaptMode": "Contain",
            "Effects": [{"Type": "Background", "SubType": "Blur", "Radius": 0.1}]
                       + ([{"Type": "Volume", "Gain": 0}] if kind == "video" else []),
        }
        if kind == "image":
            clip["Duration"] = end - start
        else:
            clip.update(In=source_start, Out=end if source_start == start else source_start + (end - start))
        return clip

    # 纯素材按源时长顺序铺满；标准模式只按命中切片切开画面，其余使用数字人。
    clips, cursor = [], 0
    if standard:
        for item, match in zip(source, matched):
            if match.matched_candidate_url is None:
                continue
            if cursor < item.start_time:
                clips.append(video(request.video_url, "video", cursor, item.start_time, cursor))
            clips.append(video(match.matched_candidate_url, match.matched_candidate_type,
                               item.start_time, item.end_time, 0))
            cursor = item.end_time
        if cursor < duration:
            clips.append(video(request.video_url, "video", cursor, duration, cursor))
    else:
        durations = TypeAdapter(list[PositiveSeconds]).validate_python(material_durations)
        if len(durations) > len(request.materials):
            raise ValueError("素材时长与请求数量不一致")
        for material, seconds in zip(request.materials, durations):
            end = min(duration, cursor + seconds)
            if abs(end - duration) < 1e-8:
                end = duration
            clips.append(video(material.file_url, material.type, cursor, end, 0))
            cursor = end
            if cursor >= duration:
                break
        if cursor < duration:
            raise ValueError("素材时长不足以覆盖成片")

    warnings = []
    def text(role: str, content: str, start: float, end: float,
             config: EffectTemplateEditor, effects: dict) -> dict:
        """使用对象文字和模板样式，短入出动画同比缩短且不跨对象区间。"""
        clip = {
            "Type": "Text", "Content": content, "TimelineIn": start,
            "TimelineOut": end, "Alignment": "Center",
            "X": min(getattr(config, f"{role}_x") / 100, 0.9999),
            "Y": min(getattr(config, f"{role}_y") / 100, 0.9999),
            "Font": "Alibaba PuHuiTi", "FontSize": getattr(config, f"{role}_size"),
            "FontColor": "#FFFFFF", "Outline": 0, "AdaptMode": "AutoWrap",
            **effects.get("bubble" if role == "bubble" else f"{role}_flower", {}),
        }
        timings = {motion: getattr(config, f"{role}_{motion}_duration")
                   for motion in ("in", "out") if f"{role}_{motion}" in effects}
        total = sum(timings.values())
        scale = min(1, (end - start) / total) if total else 1
        for motion in ("in", "out", "loop"):
            clip.update(effects.get(f"{role}_{motion}", {}))
            if motion in timings:
                seconds = floor(timings[motion] * scale * 10000 + 1e-9) / 10000
                if seconds <= 0:
                    raise ValueError("字幕太短，无法表达所选动画")
                clip[f"AaiMotion{motion.title()}"] = seconds
        return clip

    subtitle_tracks = []
    audio_tracks = [] if request.composition_mode == "materials_silent" else [{"AudioTrackClips": [{
        "MediaURL": request.audio_url, "In": 0, "Out": duration,
        "TimelineIn": 0, "TimelineOut": duration,
    }]}]
    music = request.pack_rules.background_music
    if music is not None and music.audio_switch:
        audio_tracks.append({"AudioTrackClips": [{
            "MediaURL": music.audio_url, "In": 0, "TimelineIn": 0, "TimelineOut": duration,
            "LoopMode": True, "Effects": [{"Type": "Volume", "Gain": music.volume}],
        }]})
    timeline = {"VideoTracks": [{"VideoTrackClips": clips}], "AudioTracks": audio_tracks,
                "SubtitleTracks": subtitle_tracks}
    effects = []
    tracks = list(template.tracks)
    if not standard:
        # 缺少对象时只补本次合成的默认样式，不保存模板，也不使用编辑器示例文字。
        for target, content in (("title", request.title), ("subtitle", source)):
            if content and not any(track.target == target for track in tracks):
                tracks.append(EffectTrack(id=f"composition-{target}", target=target, start_mode="seconds",
                                          start=0, duration=None, editor=EffectTemplateEditor(**{
                                              key: "" for key in ("title", "subtitle", "bubble_text") if key != target
                                          })))
    for track in tracks:
        if not standard:
            if (track.target == "subtitle" and not source) or (track.target == "title" and not request.title):
                continue
            if track.target == "title" and request.composition_mode == "materials_silent":
                track = track.model_copy(update={"start_mode": "seconds", "start": 0, "duration": None})
        track_parameters = parameters_for(track.editor)
        if track.target == "transition":
            # 忽略模板转场时间，默认一秒；每侧最多占半段，避免同一片段的入出转场重叠。
            for clip, following in zip(clips, clips[1:]):
                seconds = floor(min(1, (clip["TimelineOut"] - clip["TimelineIn"]) / 2,
                                    (following["TimelineOut"] - following["TimelineIn"]) / 2) * fps + 1e-8) / fps
                if seconds < 0.1:
                    warnings.append(f"转场对象 {track.id}：{clip['TimelineOut']:g} 秒处片段过短，已跳过转场")
                    continue
                clip["Effects"].append({"Type": "DLTransition", **track_parameters["transition"], "Duration": seconds})
            continue
        applied = resolve_track(track, duration, fps)
        if applied.notice:
            warnings.append(f"对象 {track.id}：{applied.notice}")
        if applied.end <= applied.start:
            continue
        if track.target in ("filter", "vfx"):
            effects.append({"EffectTrackItems": [{
                "Type": "Filter" if track.target == "filter" else "VFX",
                **track_parameters[track.target], "TimelineIn": applied.start, "TimelineOut": applied.end,
            }]})
        else:
            # 标题使用请求文字；字幕沿用切片关键词；气泡按对象区间显示模板文字一次。
            contents = []
            if track.target == "title":
                contents.append((request.title, "", applied.start, applied.end))
            elif track.target == "subtitle":
                for item in source:
                    keyword_used = False
                    for part in item.subtitle_parts or [item]:
                        content = "".join(char for char in part.text if char not in SUBTITLE_PUNCTUATION).strip()
                        keyword = item.keyword if not keyword_used and item.keyword in content else ""
                        keyword_used = keyword_used or bool(keyword)
                        contents.append((content, keyword,
                                         max(part.start_time, applied.start), min(part.end_time, applied.end)))
            else:
                contents.append((track.editor.bubble_text, "", applied.start, applied.end))
            text_clips = []
            for content, keyword, start, end in contents:
                if not content or not content.strip() or end <= start:
                    continue
                segment_track = track.model_copy(update={"start_mode": "seconds", "start": start, "duration": end - start})
                segment = resolve_track(segment_track, duration, fps)
                if segment.notice:
                    warnings.append(f"对象 {track.id}：{segment.notice}")
                if segment.end > segment.start:
                    if track.target in ("title", "subtitle"):
                        content = format_keyword(content, (segment.editor.title_keyword or first_title_keyword(content))
                                                 if track.target == "title" else keyword,
                                                 segment.editor, track.target)
                    # 固定标题覆盖到请求终点，避免非整帧时长留下没有标题的尾部画面。
                    end = duration if request.composition_mode == "materials_silent" and track.target == "title" else segment.end
                    text_clips.append(text(track.target, content, segment.start, end, segment.editor, track_parameters))
            if text_clips:
                timeline["SubtitleTracks"].append({"SubtitleTrackClips": text_clips})
    if effects:
        timeline["EffectTracks"] = effects
    return timeline, warnings
