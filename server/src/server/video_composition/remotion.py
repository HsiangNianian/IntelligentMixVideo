"""合成任务的 Remotion 适配：固定资产快照 → 业务实例 → 整段透明视频。

任务状态由 video_composition 保存；共享 Remotion 的隔离渲染器与名额，不建立第二套任务。
"""
import asyncio
import hashlib
import json
import logging
import math
import shutil
import traceback
from pathlib import Path
from time import perf_counter
from uuid import UUID

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from pydantic import TypeAdapter

from ..remotion_templates import sprites
from ..remotion_templates.categories import LINE_START_MAX_FRAMES, line_count
from ..remotion_templates.placement import resolve as resolve_placement
from ..template.split_screen import SplitScreen
from ..template.schema import EffectTrack, RemotionTrackEditor, active_tracks
from ..template.timing import resolve_track
from ..template.transitions import select_transitions
from .remotion_content import ContentError, dynamic_duration, resolve_config, roles
from .remotion_timing import retime_source
from ..remotion_templates.overlay_render import Overlay, OverlayError, environment_problem, fit_texts, measured, overlay_ranges, record_performance, render_overlay, to_frames
from ..remotion_templates.sprite_router import sprite_runtime
from ..remotion_templates.render_resources import RenderCapacityTimeout
from . import execution_log
from .errors import CompositionError
from .schema import CompositionRequest, Segment
from .settings import ZosSettings
from .text_events import subtitle_blocks, subtitle_events
from .zos import object_prefix, upload_public

logger = logging.getLogger(__name__)


def preserve_failure(work: Path, error: Exception) -> None:
    """清理前将私有诊断存入任务 errors/；异常正文写入成功后附加相对路径，随任务统一清理。"""
    destination = execution_log.LOG_ROOT / "video_composition" / work.name / "errors"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    detail = "".join(traceback.format_exception(error))
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        # 异常文本不含子进程输出，例如打包器缺少依赖或拒绝导入的原因只在 stderr 里。
        if isinstance(stderr := getattr(cause, "stderr", None), str) and stderr:
            detail += f"\n[{type(cause).__name__} stderr]\n{stderr[-20000:]}"
        cause = cause.__cause__ or cause.__context__
    detail = execution_log.sanitize(detail)
    (destination / "remotion-error.log").write_text(detail, encoding="utf-8")
    error.detail_log_path = "errors/remotion-error.log"
    if (work / "worker.log").is_file():
        shutil.copyfile(work / "worker.log", destination / "remotion-worker.log")


async def snapshot(template) -> dict:
    """从本单 IMS 模板的 remotion 轨道固定引用和 MySQL 发布副本，恢复时不重读。"""
    def read():
        """数据库和源码校验在工作线程内执行；空模板不初始化 Remotion 运行时。"""
        placements, publications = [], {}
        for track in active_tracks(template.tracks):
            if track.target != "remotion":
                continue
            if not template.transition_enabled and track.transition_boundary is not None:
                continue
            asset_id = str(track.sprite_id)
            if asset_id not in publications:
                publications[asset_id] = MessageToDict(sprites.get(asset_id), preserving_proto_field_name=True)
            placements.append({"id": track.id, "sprite_id": asset_id, "start": track.start,
                               "duration": track.duration, "text_target": track.text_target, "language": track.language,
                               "transition_boundary": track.transition_boundary,
                               "editor": track.editor.remotion.model_dump(mode="json", exclude_none=True) if track.editor.remotion else {}})
        return {"revision": template.updated_at.isoformat(), "placements": placements, "assets": publications}
    try:
        return await asyncio.to_thread(read)
    except Exception as exc:
        raise CompositionError("remotion_asset_unavailable", "Remotion 资产无法读取", "template") from exc


def placement_target(placement: dict, sprite: pb.PublishedSprite) -> str:
    """快照摆放的业务用途：新槽位优先，其次旧用途；视觉资产默认透明装饰，类型不符明确失败。"""
    # 旧任务固定的私有快照仍可恢复，新任务只写模板轨道参数。
    editor = RemotionTrackEditor.model_validate(placement.get("editor", {}))
    legacy = {"SPRITE_TARGET_TITLE": "title", "SPRITE_TARGET_SUBTITLE": "subtitle",
              "SPRITE_TARGET_FILTER": "decoration", "SPRITE_TARGET_VIDEO_EFFECT": "decoration"}
    target = placement.get("text_target") or editor.business_target or legacy.get(placement.get("target"))
    if target is None and sprite.kind in {pb.SPRITE_KIND_FILTER_OVERLAY, pb.SPRITE_KIND_VIDEO_OVERLAY,
                                         pb.SPRITE_KIND_TRANSITION_OVERLAY}:
        target = "decoration"
    if target is None:
        raise ContentError("请先为 Remotion 资产选择标题、字幕或透明装饰用途")
    if (sprite.kind == pb.SPRITE_KIND_TEXT) != (target in {"title", "subtitle"}):
        raise ContentError("字效请选择标题或字幕，视觉资产请选择透明装饰")
    return target


def active_placements(data: dict) -> list[dict]:
    """恢复同样以本单模板快照里的英文开关为准，旧摆放默认中文。"""
    enabled = {track["target"] for track in data.get("template", {}).get("tracks", [])
               if track.get("language") == "en" and track.get("enabled", True) and track["target"] in {"title", "subtitle"}}
    split = data.get("request", {}).get("compositionMode", "standard") == "standard" and (data.get("template", {}).get("split_screen") or {}).get("enabled")
    return [item for item in (data.get("remotion") or {}).get("placements", [])
            if not (split and item.get("transition_boundary") is not None)
            if item.get("language", "zh") != "en" or item.get("text_target") in enabled]


async def require_environment(data: dict) -> None:
    """快照里有会实际渲染的摆放时，在付费 ASR／切片前确认隔离渲染环境；没有可显示内容时不要求。"""
    saved = data.get("remotion") or {}
    placements = active_placements(data)
    if data.get("template", {}).get("transition_enabled") is False:
        placements = [item for item in placements if item.get("transition_boundary") is None]
    if not placements:
        return
    request = CompositionRequest.from_snapshot(data["request"])
    try:
        targets = {placement_target(placement, ParseDict(saved["assets"][placement["sprite_id"]], pb.PublishedSprite()))
                   for placement in placements}
    except ContentError as exc:
        raise CompositionError("remotion_asset_not_adapted", str(exc), "template") from exc
    title = bool(request.title and request.title.strip())
    subtitle = bool(request.text) and request.composition_mode != "materials_silent"
    if not ("decoration" in targets or ("title" in targets and title) or ("subtitle" in targets and subtitle)):
        return
    try:
        problem = environment_problem((await sprite_runtime()).settings)
    except Exception as exc:
        raise CompositionError("remotion_render_environment_unavailable", "Remotion 渲染环境不可用", "template") from exc
    if problem:
        raise CompositionError("remotion_render_environment_unavailable", "Remotion 渲染环境不可用", "template")


def plan(data: dict, max_seconds: float, *, timeline: dict | None = None, transition_seed: str = "") -> Overlay | None:
    """根据已保存业务快照展开字幕；示例时长不限制字幕，空内容不产生实例。"""
    saved = data.get("remotion")
    # 历史任务没有快照时延续原流程，不能恢复时偷偷读取新的模板绑定。
    if not saved or not active_placements(data):
        return None
    request = CompositionRequest.from_snapshot(data["request"])
    duration = data["duration_ms"] / 1000
    if not math.isfinite(duration) or duration <= 0:
        raise OverlayError("duration_invalid", "成片时长无效")
    width, height, fps = (data["output"][key] for key in ("width", "height", "fps"))
    frames = max(1, math.ceil(duration * fps - 1e-8))
    publications = {key: ParseDict(value, pb.PublishedSprite()) for key, value in saved["assets"].items()}
    segments = TypeAdapter(list[Segment]).validate_python(data.get("segmentation", {}).get("segments", []))
    spoken = request.composition_mode != "materials_silent" and bool(request.text)
    title_keyword = data.get("segmentation", {}).get("title_keyword", "")
    instances = []
    timed_sources = {}
    placements = sorted(active_placements(data), key=lambda item: item.get("order", 0))
    template = data.get("template", {})
    layout = SplitScreen.model_validate(template["split_screen"]) if request.composition_mode == "standard" and (template.get("split_screen") or {}).get("enabled") else None
    intervals = []
    if layout:
        matched_ids = {item["segment_id"] for item in data.get("matches", []) if item.get("matched_candidate_url")}
        for segment in segments:
            if segment.segment_id in matched_ids:
                a, b = to_frames(segment.start_time, fps), to_frames(segment.end_time, fps)
                if intervals and intervals[-1][1] == a:
                    intervals[-1] = (intervals[-1][0], b)
                else:
                    intervals.append((a, b))
    english_subtitle = any(track.get("target") == "subtitle" and track.get("language") == "en" and track.get("enabled", True)
                           for track in template.get("tracks", []))
    if template.get("transition_enabled") is False:
        placements = [item for item in placements if item.get("transition_boundary") is None]
    if template.get("transition_mode") is not None and any(item.get("transition_boundary") is not None for item in placements):
        if timeline is None:
            raise ContentError("Remotion 转场缺少最终素材时间线")
        selected = select_transitions(template["tracks"], len(timeline["VideoTracks"][0]["VideoTrackClips"]) - 1,
                                      template["transition_mode"], transition_seed)
        expanded = []
        for placement in placements:
            if placement.get("transition_boundary") is None:
                expanded.append(placement)
            else:
                expanded.extend({**placement, "transition_boundary": boundary}
                                for item, boundary in selected if item["id"] == placement["id"])
        placements = expanded
    for placement in placements:
        editor = RemotionTrackEditor.model_validate(placement.get("editor", {}))
        sprite = publications[placement["sprite_id"]]
        language = placement.get("language", "zh")
        text_target = placement.get("text_target")
        target = placement_target(placement, sprite)
        if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
            raise ContentError("Remotion 资产快照源码校验失败")
        try:
            config, host = resolve_placement(sprite, editor)
        except ValueError as exc:
            raise ContentError(str(exc)) from exc
        dynamic = target in {"title", "subtitle"}
        fixed_clock = dynamic and not dynamic_duration(json.loads(sprite.config_schema_json))
        start = placement.get("start", 0)
        length_seconds = placement.get("duration") or sprite.canvas.preview_frames / sprite.canvas.fps
        if text_target:
            # 新槽位跟随文字对象的实际区间；IMS 动画不参与 Remotion 字效的时长校验。
            text_track = next((track for track in data.get("template", {}).get("tracks", [])
                               if track.get("target") == text_target and track.get("language", "zh") == language), None)
            if text_track is None:
                raise ContentError("Remotion 字效缺少所作用的文字对象")
            track = EffectTrack.model_validate(text_track).model_copy(update={"target": "remotion"})
            applied = resolve_track(track, duration, fps)
            end = duration if text_target == "title" and track.duration is None and applied.end > applied.start else applied.end
            start, length_seconds = applied.start, end - applied.start
        boundary = placement.get("transition_boundary")
        if boundary is not None:
            if sprite.kind != pb.SPRITE_KIND_TRANSITION_OVERLAY:
                raise ContentError("只有 Remotion 转场可以指定视频交界处")
            if timeline is None:
                raise ContentError("Remotion 转场缺少最终素材时间线")
            clips = timeline["VideoTracks"][0]["VideoTrackClips"]
            if boundary >= len(clips):
                continue
            if template.get("transition_mode") is not None:
                length_seconds = min(math.floor(length_seconds * fps + 0.5),
                    math.floor((clips[boundary - 1]["TimelineOut"] - clips[boundary - 1]["TimelineIn"]) * fps + 1e-8) // 2,
                    math.floor((clips[boundary]["TimelineOut"] - clips[boundary]["TimelineIn"]) * fps + 1e-8) // 2) / fps
                if length_seconds < 0.1:
                    continue
            start = max(0, clips[boundary - 1]["TimelineOut"] - length_seconds / 2)
        lines = line_count(json.loads(sprite.config_schema_json)) if sprite.kind == pb.SPRITE_KIND_TEXT else 1
        # 英文标题和字幕的字效按单行设计：译文过长时排版预检缩到能单行显示的最大字号，避免换行压到另一语言的文字。
        one_line = lines == 1 and language == "en" and target in {"title", "subtitle"}
        # 字幕轨的文字换行时朝远离另一语言字幕的方向展开：英文向下，中文字幕在启用英文字幕时向上，两条字幕因此不会
        # 互相压住；没有启用英文字幕时中文保持原本的居中展开（上下同时长）。
        grow = None
        if lines == 1 and target == "subtitle":
            grow = "down" if language == "en" else "up" if english_subtitle else None
        # 每项内容是 (各行文字, 各行关键词, 各行出现的秒数, 开始, 结束)；单行字效只有一行。
        if target == "subtitle":
            # 多行字效把同一句里最多 lines 个连续切片合成一组：逐行出现，整组在最后一个切片结束时清空。
            text_schema = json.loads(sprite.config_schema_json).get("properties", {}).get(sprite.text_prop or "text", {})
            language_events = subtitle_events(segments, language) if spoken else []
            blocks = subtitle_blocks(segments, lines, text_schema.get("maxLength"), (LINE_START_MAX_FRAMES - 1) / fps, language=language) \
                if spoken and lines > 1 else [[event] for event in language_events]
            low, high = (start, start + length_seconds) if text_target else (0, duration)
            contents = []
            for block in blocks:
                # 新槽位只显示文字对象区间内的部分，区间外的切片（行）丢弃。
                shown = [(text, keyword, max(begin, low), min(end, high)) for text, keyword, begin, end in block]
                shown = [event for event in shown if min(duration, event[3]) > max(0, event[2])]
                if shown:
                    contents.append(([item[0] for item in shown], [item[1] for item in shown], [item[2] for item in shown],
                                     shown[0][2], shown[-1][3]))
        elif target == "title":
            # 多行字效按标题里的换行分行（空行忽略，多出的并入最后一行），所有行同时出现；没有换行时整段放在第一行，
            # 其余行没有文字、不显示。
            title = data.get("segmentation", {}).get("title_en") if language == "en" else request.title
            keyword = data.get("segmentation", {}).get("title_keyword_en", "") if language == "en" else title_keyword
            if language == "en" and request.title and request.title.strip() and not (title and title.strip()):
                raise ContentError("缺少英文标题译文")
            parts = [part for part in title.split("\n") if part.strip()] if lines > 1 and title else []
            texts = parts[:lines - 1] + [" ".join(parts[lines - 1:])] if len(parts) > lines else parts or [title]
            contents = [(texts, [keyword], [start] * len(texts), start, start + length_seconds)] if request.title and request.title.strip() else []
        else:
            if sprite.kind == pb.SPRITE_KIND_TEXT or roles(json.loads(sprite.config_schema_json))["text"]:
                raise ContentError("文字资产必须选择标题或字幕，不能作为装饰渲染示例文案")
            contents = [([""], [""], [start], start, start + length_seconds)]
        for texts, keywords, starts, start, end in contents:
            start, end = max(0, start), min(duration, end)
            if end <= start:
                continue
            first = min(frames - 1, to_frames(start, fps))
            last = min(frames, max(first + 1, to_frames(end, fps)))
            count = last - first
            # 多行字效的换行只用来分行：每一行自己的文字不能再含换行，否则会挤进下一行的样式里。
            text = "\n".join(item.replace("\n", " ") for item in texts) if lines > 1 else texts[0]
            # 各行出现的帧，从实例起点算起；固定时钟资产读到的是预览时钟，按宿主同样的线性映射换算，
            # 两帧以内会被定格，所有行同时显示。
            offsets = [min(count - 1, max(0, to_frames(item, fps) - first)) for item in starts]
            preview = sprite.canvas.preview_frames
            clock = [round(item * (preview - 1) / (count - 1)) if count > 2 else 0 for item in offsets] if fixed_clock else offsets
            values = resolve_config(sprite, text, keywords, preview if fixed_clock else count,
                                    placement.get("text_prop", ""), placement.get("keywords_prop", ""), config=config,
                                    line_starts=clock) if dynamic else config
            code = sprite.tsx_code
            if fixed_clock:
                if sprite.sprite_id not in timed_sources:
                    timed_sources[sprite.sprite_id] = retime_source(sprite)
                code = timed_sources[sprite.sprite_id]
            # 装饰保留完整动画区间，由成片总长裁尾；动态正文使用实际可见区间。
            length = count if dynamic else max(1, to_frames(min(length_seconds, max_seconds), fps))
            # 资产与实际正文供渲染前的排版预检：正文可能写入多个参数路径，这里给出唯一的显示文字。
            # 多行字效在最后一行出现后才完整，排版预检只测量这之后的帧。
            instance = {"sprite_id": placement["sprite_id"], **({"text": text} if dynamic else {}),
                              **({"reveal_frame": offsets[-1]} if dynamic and lines > 1 else {}),
                              **({"one_line": True} if one_line else {}),
                              **({"grow": grow} if grow else {}),
                              "code": code, "config": values, "host": host, "start_frame": first,
                              "duration_frames": length, "width": sprite.canvas.width, "height": sprite.canvas.height}
            if layout and target == "subtitle" and intervals:
                point = layout.subtitle_en if language == "en" else layout.subtitle
                moved = {**host, "transform": {**host["transform"], "x": point.x * sprite.canvas.width / 100,
                    "y": point.y * sprite.canvas.height / 100 + host["box"]["height"] * host["transform"].get("scale", 1) / 2}}
                cuts = sorted({first, last, *(frame for interval in intervals for frame in interval if first < frame < last)})
                for a, b in zip(cuts, cuts[1:]):
                    # 可见区间与动画区间分开：跨布局的多行字幕保持原起点、长度和逐行时钟。
                    instances.append({**instance, "visible_start_frame": a, "visible_end_frame": b,
                                      "host": moved if any(left <= a < right for left, right in intervals) else host})
            else:
                instances.append(instance)
    if not instances:
        return None
    if duration > max_seconds:
        raise OverlayError("duration_too_long", f"成片时长超过 Remotion 上限 {max_seconds:g} 秒")
    return Overlay(width, height, fps, frames, instances)


async def render(task_id: str, data: dict, timeout: float, *, timeline: dict | None = None) -> dict | None:
    """使用共享渲染名额输出整段视频并上传；返回后由合成任务保存，失败不降级。"""
    if not data.get("remotion", {}).get("placements"):
        return None
    try:
        service = await sprite_runtime()
        work = service.store.root / "composition-overlays" / task_id
        started = perf_counter()
        # 计划与打包失败同样私有保存根因：公开错误只有固定摘要，打包器输出可能含资产源码。
        try:
            await asyncio.to_thread(shutil.rmtree, work, True)
            overlay = await measured(work, "plan", asyncio.to_thread(plan, data, service.settings.overlay_max_seconds, timeline=timeline, transition_seed=task_id))
            if overlay is None:
                return None
            if environment_problem(service.settings, overlay):
                raise OverlayError("render_environment_unavailable", "Remotion 渲染环境不可用")
            publications = {key: ParseDict(value, pb.PublishedSprite()) for key, value in data["remotion"]["assets"].items()}
            waiting = perf_counter()
            requested = len(overlay_ranges(overlay, service.settings))
            async with service.overlays.slot, service.overlays.capacity.claim(requested, service.settings.overlay_frame_concurrency) as grant:
                await asyncio.to_thread(record_performance, work, slot_wait_ms=max(0, (perf_counter() - waiting) * 1000 - grant.wait_ms),
                                        resource_wait_ms=grant.wait_ms, granted_chunks=grant.chunks, reserved_memory_mb=grant.reserved_memory_mb)
                # 与叠加渲染相同：放不下的标题、字幕缩小字号，缩到最小仍放不下以 remotion_text_too_long 失败。
                await measured(work, "text_fit", fit_texts(service.harness.renderer, overlay, publications, work / "fit"))
                output = await render_overlay(service.harness.renderer, overlay, work, chunk_concurrency=grant.chunks, frame_concurrency=grant.pages)
            # 文件必须存活到上传线程收尾；取消任务不保存迟到结果。
            uploading = perf_counter()
            upload = asyncio.create_task(asyncio.to_thread(upload_public,
                [(output, f"{object_prefix(data)}/{task_id}/overlay.webm", "video/webm")], ZosSettings(), timeout))
            try:
                urls = await asyncio.shield(upload)
            finally:
                await asyncio.gather(upload, return_exceptions=True)
                await asyncio.to_thread(record_performance, work, upload_ms=(perf_counter() - uploading) * 1000)
            return {"video_url": urls[0], "duration": data["duration_ms"] / 1000,
                    "width": overlay.width, "height": overlay.height, "fps": overlay.fps, "frames": overlay.frames}
        except Exception as exc:
            try:
                await asyncio.to_thread(preserve_failure, work, exc)
            except OSError as diagnostic_error:
                logger.error("合成任务 %s 私有渲染诊断保存失败：%s", task_id, type(diagnostic_error).__name__)
            raise
        finally:
            performance = await asyncio.to_thread(record_performance, work, total_ms=(perf_counter() - started) * 1000)
            logger.info("Remotion performance task_id=%s %s", task_id, json.dumps(performance, ensure_ascii=False))
            await asyncio.to_thread(shutil.rmtree, work, True)
    except ContentError as exc:
        raise CompositionError("remotion_asset_not_adapted", str(exc), "assembling") from exc
    except RenderCapacityTimeout as exc:
        raise CompositionError("remotion_resource_unavailable", str(exc), "assembling") from exc
    except OverlayError as exc:
        raise CompositionError("remotion_" + exc.code, exc.message, "assembling") from exc
    except Exception as exc:
        raise CompositionError("remotion_render_failed", "Remotion 字效渲染或上传失败", "assembling") from exc


def append_track(timeline: dict, result: dict | None, output: dict) -> None:
    """直接加入最后视频轨；IMS 文字位于其上层，透明视频从 0 秒与音频对齐。"""
    if result:
        timeline["VideoTracks"].append({"VideoTrackClips": [{
            "Type": "Video", "MediaURL": result["video_url"], "In": 0, "Out": result["duration"],
            "TimelineIn": 0, "TimelineOut": result["duration"], "Width": output["width"],
            "Height": output["height"], "AdaptMode": "Cover",
        }]})
