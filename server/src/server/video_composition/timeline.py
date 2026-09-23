"""由业务快照生成 IMS Timeline；转场只用效果类型连接实际片段，其他对象保留模板时间规则。"""

from math import floor

from pydantic import TypeAdapter

from ..segmentation.segmentation import SUBTITLE_PUNCTUATION
from ..template.schema import CATEGORY_PARAMETERS, EffectTemplateEditor, Template
from ..template.timing import resolve_track
from .schema import CompositionRequest, MatchedSegment, Segment


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
    """逐项核对上游整数编号、顺序、文案和秒制边界，拒绝其他任务或重新对齐的结果。"""
    if len(segments) != len(matches):
        raise ValueError("匹配片段数量不一致")
    for source, matched in zip(segments, matches):
        if (
            matched.segment_id != source.segment_id or matched.text != source.text
            or matched.start_time != source.start_time
            or matched.end_time != source.end_time
        ):
            raise ValueError("匹配片段编号、文案或时间与切片不一致")


def build_timeline(
    request: dict, template: dict, segments: list[dict], matches: list[dict],
    duration_ms: int, *, width: int, height: int, fps: int,
) -> tuple[dict, list[str]]:
    """返回时间线及转场调整说明；无网络、模型、随机值或对输入快照的修改。"""
    request = CompositionRequest.model_validate(request)
    template = Template.model_validate(template)
    source = TypeAdapter(list[Segment]).validate_python(segments)
    matched = TypeAdapter(list[MatchedSegment]).validate_python(matches)
    validate_segments(source, duration_ms)
    validate_matches(source, matched)
    if not all(type(v) is int and v > 0 for v in (width, height, fps)) or fps > 60:
        raise ValueError("输出尺寸或帧率无效")
    duration = duration_ms / 1000
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

    # 只按命中素材切开可见画面；连续未命中字幕和所有间隙合为原时刻数字人。
    clips, cursor = [], 0
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

    warnings = []
    def text(role: str, content: str, start: float, end: float,
             config: EffectTemplateEditor, effects: dict) -> dict:
        """使用业务文字和模板样式，短入出动画同比缩短且不跨字幕区间。"""
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
    audio_tracks = [{"AudioTrackClips": [{
        "MediaURL": request.audio_url, "In": 0, "Out": duration,
        "TimelineIn": 0, "TimelineOut": duration,
    }]}]
    music = request.pack_rules.background_music
    if music.audio_switch:
        audio_tracks.append({"AudioTrackClips": [{
            "MediaURL": music.audio_url, "In": 0, "TimelineIn": 0, "TimelineOut": duration,
            "LoopMode": True, "Effects": [{"Type": "Volume", "Gain": music.volume}],
        }]})
    timeline = {"VideoTracks": [{"VideoTrackClips": clips}], "AudioTracks": audio_tracks,
                "SubtitleTracks": subtitle_tracks}
    effects = []
    for track in template.tracks:
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
            # 标题使用请求文字；字幕使用标点处细分的短句，关键词仍使用原切片。
            items = [part for item in source for part in (item.subtitle_parts or [item])] if track.target == "subtitle" else source
            contents = [(request.title, applied.start, applied.end)] if track.target == "title" else [
                (item.text if track.target == "subtitle" else item.keyword,
                 max(item.start_time, applied.start), min(item.end_time, applied.end)) for item in items
            ]
            text_clips = []
            for content, start, end in contents:
                if track.target == "subtitle":
                    content = "".join(char for char in content if char not in SUBTITLE_PUNCTUATION).strip()
                if not content or not content.strip() or end <= start:
                    continue
                segment_track = track.model_copy(update={"start_mode": "seconds", "start": start, "duration": end - start})
                segment = resolve_track(segment_track, duration, fps)
                if segment.notice:
                    warnings.append(f"对象 {track.id}：{segment.notice}")
                if segment.end > segment.start:
                    text_clips.append(text(track.target, content, segment.start, segment.end, segment.editor, track_parameters))
            if text_clips:
                timeline["SubtitleTracks"].append({"SubtitleTrackClips": text_clips})
    if effects:
        timeline["EffectTracks"] = effects
    return timeline, warnings
