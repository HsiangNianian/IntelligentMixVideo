"""由业务快照生成 IMS Timeline；纯素材按序裁尾，标题沿用模板区间，字幕使用切片正文与时间。"""

import json
from math import floor

from pydantic import TypeAdapter

from .text_events import subtitle_events
from .transitions import keyword_transitions
from ..remotion_templates.categories import bilingual as is_bilingual
from ..template.schema import CATEGORY_PARAMETERS, EffectTemplateEditor, EffectTrack, Template, active_tracks
from ..template.timing import ClipContext, TimelineContext, resolve_template, resolve_track
from ..template.transitions import select_transitions
from .schema import CompositionRequest, MatchedSegment, PositiveSeconds, Segment
from .split_screen import window_clip, border_clip


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


def _bilingual_asset(assets: dict, sprite_id) -> bool:
    """该片段引用的字效是否是双语字效：声明了英文文字参数 `textEn`，同时显示中英两种语言。"""
    published = assets.get(str(sprite_id))
    if not isinstance(published, dict):
        return False
    try:
        return is_bilingual(json.loads(published.get("config_schema_json") or "{}"))
    except (TypeError, ValueError):
        return False


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
    material_durations: list[float] | None = None, title_keyword: str = "", transition_seed: str = "",
    title_en: str | None = None, title_keyword_en: str = "",
    frame_mask_url: str | None = None,
    split_assets: dict | None = None, avatar_opening: dict | None = None,
    use_keyword_transitions: bool = False, remotion_assets: dict | None = None,
) -> tuple[dict, list[str]]:
    """返回时间线及转场说明；标题关键词仅取切片返回值，缺失不强调，不修改输入快照。"""
    request = CompositionRequest.from_snapshot(request)
    template = Template.model_validate(template)
    template = template.model_copy(update={"tracks": active_tracks(template.tracks)})
    standard = request.composition_mode == "standard"
    has_subtitles = request.composition_mode != "materials_silent" and bool(request.text)
    source = TypeAdapter(list[Segment]).validate_python(segments) if has_subtitles else []
    matched = TypeAdapter(list[MatchedSegment]).validate_python(matches) if standard else []
    layout = template.split_screen if standard and template.split_screen and template.split_screen.enabled else None
    opening = avatar_opening if standard and template.avatar_effects and template.avatar_effects.opening_enabled else None
    opening_end = opening["duration"] if opening else 0
    circle = avatar_opening if layout and layout.layout == "circle" else None
    circle_missing = bool(layout and layout.layout == "circle" and not circle)
    cutout = bool(layout and layout.layout != "circle" and template.avatar_effects and template.avatar_effects.remove_background)
    material_clips, material_borders, avatar_clips, avatar_borders = [], [], [], []
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
            material = video(match.matched_candidate_url, match.matched_candidate_type, item.start_time, item.end_time, 0)
            if layout and not circle_missing:
                if split_assets is None:
                    raise ValueError("同屏布局图片尚未准备")
                if layout.layout == "split":
                    material = window_clip(material, layout.material, split_assets["media"][match.matched_candidate_url], width, height)
                    material_clips.append(material)
                    if border := split_assets["material_borders"].get(match.matched_candidate_url):
                        material_borders.append(border_clip(material, border))
                avatar_start = max(item.start_time, opening_end)
                if avatar_start < item.end_time and avatar_clips and avatar_clips[-1]["TimelineOut"] == avatar_start:
                    avatar_clips[-1].update(TimelineOut=item.end_time, Out=item.end_time)
                    if avatar_borders:
                        avatar_borders[-1].update(TimelineOut=item.end_time, Duration=item.end_time - avatar_borders[-1]["TimelineIn"])
                elif avatar_start < item.end_time:
                    avatar = window_clip(video(request.video_url, "video", avatar_start, item.end_time, avatar_start),
                                         layout.avatar if layout.layout == "split" else layout.avatar.model_copy(update={"radius": 0, "fit": "Contain"}),
                                         split_assets["media"][request.video_url], width, height, remove_background=cutout,
                                         circle=circle, feather=template.avatar_effects.feather if template.avatar_effects else 0.02)
                    avatar_clips.append(avatar)
                    if layout.layout == "split" and not cutout and (border := split_assets.get("avatar_border")):
                        avatar_borders.append(border_clip(avatar, border))
                # 分区用底色占位，叠加布局直接保留完整素材及模糊填充；业务时间不变。
                if layout.layout == "split":
                    material = {"Type": "Image", "MediaURL": split_assets["background"], "Duration": item.end_time - item.start_time,
                                "TimelineIn": item.start_time, "TimelineOut": item.end_time, "Width": width, "Height": height, "AdaptMode": "Fill"}
            clips.append(material)
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
            "Font": "Alibaba PuHuiTi" if role == "bubble" else getattr(config, f"{role}_font"),
            "FontSize": getattr(config, f"{role}_size"),
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
    overlay_tracks = [{"VideoTrackClips": items} for items in (material_clips, material_borders, avatar_clips, avatar_borders) if items]
    if opening:
        # 覆盖原业务轨，不拆素材、不改变转场数量；原素材在遮挡期间仍按原进度播放。
        w, h = min(width, height * opening["aspect"]), min(height, width / opening["aspect"])
        diameter = opening["radius"] * 2
        overlay_tracks.extend([
            {"VideoTrackClips": [{"Type": "Image", "MediaURL": opening["background"], "Duration": opening_end,
                "TimelineIn": 0, "TimelineOut": opening_end, "Width": width, "Height": height, "AdaptMode": "Fill"}]},
            {"VideoTrackClips": [{**video(request.video_url, "video", 0, opening_end, 0),
                "X": round((width - w) / 2), "Y": round((height - h) / 2), "Width": round(w), "Height": round(h), "AdaptMode": "Cover",
                "Effects": [{"Type": "Volume", "Gain": 0}, {"Type": "VFX", "SubType": "mask_circle", "ExtParams":
                    f'x={opening["x"]:.8f},y={opening["y"]:.8f},width={diameter:.8f},height={diameter * opening["aspect"]:.8f},antialias={template.avatar_effects.feather:.6f}'}]}]},
        ])
    # 仅修正同屏和开场创建的轨道；IMS 出点包含该时刻，减去接口最小精度表达 [start,end)。
    # 只改显示终点，源 In/Out、业务边界与总时长不变，避免切换帧出现大小两个人像。
    for row in overlay_tracks:
        for clip in row["VideoTrackClips"]:
            if clip["TimelineOut"] < duration:
                clip["TimelineOut"] = round(clip["TimelineOut"] - 0.0001, 4)
    timeline["VideoTracks"].extend(overlay_tracks)
    if template.frame_mask and any(bar.enabled and floor(height * bar.height / 100 + 0.5) > 0 for bar in (template.frame_mask.top, template.frame_mask.bottom)):
        if not frame_mask_url:
            raise ValueError("上下蒙版图片尚未准备完成")
        # 蒙版覆盖同屏、数字人开场等视频层；全局效果、Remotion 与文字仍在其上。
        timeline["VideoTracks"].append({"VideoTrackClips": [{
            "Type": "Image", "MediaURL": frame_mask_url, "TimelineIn": 0, "TimelineOut": duration,
            "Duration": duration, "X": 0, "Y": 0, "Width": width, "Height": height, "AdaptMode": "Fill",
        }]})
    effects = []
    # Remotion 片段由叠加视频服务渲染，不属于 IMS 时间线，也没有 IMS 效果可校验；
    # 使用 Remotion 字效的标题或字幕同样由叠加视频显示，IMS 不再渲染这段文字。
    # 双语字效（双语字幕）自己显示中英两种语言，它所作用对象的英文文字也不交给 IMS。
    remotion_texts = {(track.text_target, track.language) for track in template.tracks if track.text_target is not None}
    remotion_texts |= {(track.text_target, "en") for track in template.tracks
                       if track.target == "remotion" and track.text_target is not None and track.language != "en"
                       and _bilingual_asset(remotion_assets or {}, track.sprite_id)}
    tracks = [track for track in template.tracks if track.target != "remotion" and (track.target, track.language) not in remotion_texts]
    if not template.transition_enabled or layout:
        tracks = [track for track in tracks if track.target != "transition"]
    elif use_keyword_transitions or template.transition_mode is not None:
        selected = keyword_transitions(template.model_dump(mode="json"), clips, source, matches,
                                       standard=standard, fps=fps, seed=transition_seed, assets=remotion_assets or {}) \
            if use_keyword_transitions else select_transitions(
                [track.model_dump(mode="json") for track in template.tracks],
                len(clips) - 1, template.transition_mode, transition_seed)
        by_track = {track.id: track for track in template.tracks}
        tracks = [track for track in tracks if track.target != "transition"] + [
            by_track[item["id"]].model_copy(update={"id": f"{item['id']}:{boundary}",
                "transition_boundary": boundary,
                "duration": item["duration"] if use_keyword_transitions else template.transition_duration_seconds})
            for item, boundary in selected if item["target"] == "transition"
        ]
    if not standard:
        tracks = [track for track in tracks
                  if not ((track.target == "subtitle" and not source) or (track.target == "title" and not request.title))]
        # 缺少对象时只补本次合成的默认样式，不保存模板，也不使用编辑器示例文字。
        for target, content in (("title", request.title), ("subtitle", source)):
            if content and (target, "zh") not in remotion_texts and not any(track.target == target and track.language == "zh" for track in tracks):
                tracks.append(EffectTrack(id=f"composition-{target}", target=target, start_mode="seconds",
                                          start=0, duration=None, editor=EffectTemplateEditor(**{
                                              key: "" for key in ("title", "subtitle", "bubble_text") if key != target
                                          })))
    layers = {"title": 0, "subtitle": 1, "bubble": 2, "transition": 3, "vfx": 4, "filter": 5}
    resolved = resolve_template(template.model_copy(update={"tracks": tracks}), TimelineContext(
        layout="fixed", fps=fps, duration=duration,
        clips=[ClipContext(start=clip["TimelineIn"], duration=clip["TimelineOut"] - clip["TimelineIn"]) for clip in clips],
    ))
    warnings.extend(resolved.notices)
    for track in sorted(tracks, key=lambda item: layers[item.target]):
        track_parameters = parameters_for(track.editor)
        applied = resolved.tracks[track.id]
        if track.target == "transition":
            if applied.end > applied.start:
                clips[track.transition_boundary - 1]["Effects"].append({
                    "Type": "DLTransition", **track_parameters["transition"], "Duration": round(applied.end - applied.start, 10),
                })
            continue
        if applied.end <= applied.start:
            continue
        if track.target in ("filter", "vfx"):
            effects.append({"EffectTrackItems": [{
                "Type": "Filter" if track.target == "filter" else "VFX",
                **track_parameters[track.target], "TimelineIn": applied.start, "TimelineOut": applied.end,
            }]})
        else:
            # 标题使用请求文字，标题与字幕关键词均取切片结果；气泡显示模板文字一次。
            contents = []
            if track.target == "title":
                if track.language == "en" and request.title and request.title.strip() and not (title_en and title_en.strip()):
                    raise ValueError("缺少英文标题译文")
                contents.append((title_en if track.language == "en" else request.title,
                                 title_keyword_en if track.language == "en" else title_keyword, applied.start, applied.end))
            elif track.target == "subtitle":
                contents.extend((content, keyword, max(start, applied.start), min(end, applied.end))
                                for content, keyword, start, end in subtitle_events(source, track.language))
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
                        content = format_keyword(content, keyword, segment.editor, track.target)
                    # 持续到结束的标题覆盖请求终点，固定时长标题遵循用户设置。
                    end = duration if track.target == "title" and track.duration is None else segment.end
                    clip = text(track.target, content, segment.start, end, segment.editor, track_parameters)
                    same_screen = layout and not circle_missing and track.target == "subtitle" and any(
                        item.start_time <= segment.start < item.end_time and match.matched_candidate_url
                        for item, match in zip(source, matched)
                    )
                    if track.language == "en":
                        clip.update(AdaptMode="AutoScale", TextWidth=0.9)
                    cuts = [segment.start, end]
                    if same_screen and segment.start < opening_end < end:
                        cuts.insert(1, opening_end)
                    for left, right in zip(cuts, cuts[1:]):
                        part = {**clip, "TimelineIn": left,
                                "TimelineOut": round(right - 0.0001, 4) if same_screen and right < duration else right}
                        if same_screen and left >= opening_end:
                            point = layout.subtitle_en if track.language == "en" else layout.subtitle
                            part.update(X=min(point.x / 100, 0.9999), Y=min(point.y / 100, 0.9999))
                        # 布局切点不触发额外入出场；花字与循环样式保留。
                        for motion, cropped in (("In", left > segment.start), ("Out", right < end)):
                            if cropped:
                                part.pop(f"AaiMotion{motion}Effect", None)
                                part.pop(f"AaiMotion{motion}", None)
                            elif f"AaiMotion{motion}" in part:
                                part[f"AaiMotion{motion}"] = min(part[f"AaiMotion{motion}"], right - left)
                        text_clips.append(part)
            if text_clips:
                timeline["SubtitleTracks"].append({"SubtitleTrackClips": text_clips})
    if effects:
        timeline["EffectTracks"] = effects
    return timeline, warnings
