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
from uuid import UUID

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict, ParseDict
from pydantic import TypeAdapter

from ..remotion_templates import sprites
from ..remotion_templates.categories import LINE_START_MAX_FRAMES, line_count
from ..remotion_templates.placement import resolve as resolve_placement
from ..template.schema import KEYWORD_TARGETS, EffectTrack, RemotionTrackEditor, active_tracks, text_role
from ..template.timing import resolve_track
from ..template.transitions import select_transitions
from .remotion_content import ContentError, dynamic_duration, resolve_config, roles
from .remotion_timing import retime_source
from ..remotion_templates.overlay_render import Overlay, OverlayError, environment_problem, fit_texts, render_overlay, to_frames
from ..remotion_templates.sprite_router import sprite_runtime
from . import execution_log
from .errors import CompositionError
from .schema import CompositionRequest, Segment
from .settings import ZosSettings
from .text_events import subtitle_blocks, subtitle_events
from .zos import upload_public

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
    if (sprite.kind == pb.SPRITE_KIND_TEXT) != (text_role(target) in {"title", "subtitle"}):
        raise ContentError("字效请选择标题、字幕或它们的关键词，视觉资产请选择透明装饰")
    return target


def active_placements(data: dict) -> list[dict]:
    """恢复同样以本单模板快照里的英文与关键词开关为准，旧摆放默认中文。"""
    disabled = {(track["target"], track.get("language", "zh")) for track in data.get("template", {}).get("tracks", [])
                if track.get("enabled", True) is False}
    return [item for item in (data.get("remotion") or {}).get("placements", [])
            if (item.get("text_target"), item.get("language", "zh")) not in disabled]


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
        # 关键词对象跟随所属文字：切片前不知道是否有关键词，按所属文字是否存在要求环境。
        targets = {text_role(placement_target(placement, ParseDict(saved["assets"][placement["sprite_id"]], pb.PublishedSprite())))
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
    """根据已保存业务快照展开字幕；示例时长不限制字幕，空内容不产生实例。

    关键词对象设为句中原位（`in_place`）时，所属文字的实例带 `anchor`，关键词字效实例带 `anchored`，
    由叠加 worker 画在所属文字字效标记的关键词上（见 `remotion/overlay-worker.mjs`）。
    """
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
    # 句中原位：所属文字的字效实例是锚点，关键词字效排在全部实例之后，逐帧画在锚点标记的关键词上，
    # 从关键词所在切片开始，到覆盖它的锚点实例结束（多行字效整组清空时一起消失）。
    in_place = {KEYWORD_TARGETS[track["target"]]: track["target"] for track in template.get("tracks", [])
                if track.get("target") in KEYWORD_TARGETS and track.get("in_place") and track.get("enabled", True)}
    anchors: dict[str, list[tuple[str, int, int]]] = {role: [] for role in in_place}
    placements = sorted(placements, key=lambda item: item.get("text_target") in in_place.values())
    for placement in placements:
        editor = RemotionTrackEditor.model_validate(placement.get("editor", {}))
        sprite = publications[placement["sprite_id"]]
        language = placement.get("language", "zh")
        text_target = placement.get("text_target")
        target = placement_target(placement, sprite)
        anchoring = target in in_place.values()
        if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
            raise ContentError("Remotion 资产快照源码校验失败")
        try:
            config, host = resolve_placement(sprite, editor)
        except ValueError as exc:
            raise ContentError(str(exc)) from exc
        dynamic = text_role(target) in {"title", "subtitle"}
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
            end = duration if text_role(text_target) == "title" and track.duration is None and applied.end > applied.start else applied.end
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
        if target in KEYWORD_TARGETS and lines > 1:
            raise ContentError("多行字效不能用于关键词对象")
        # 每项内容是 (各行文字, 各行关键词, 各行出现的秒数, 开始, 结束)；单行字效只有一行。
        if target == "subtitle_keyword":
            # 关键词对象在每个带关键词的字幕区间里只显示这个词，字效内部不再强调关键词。
            events = subtitle_events(segments, language) if spoken else []
            shown = [(keyword, max(begin, start), min(end, start + length_seconds)) for _, keyword, begin, end in events if keyword]
            contents = [([keyword], [""], [begin], begin, end) for keyword, begin, end in shown if min(duration, end) > max(0, begin)]
        elif target == "title_keyword":
            contents = [([title_keyword], [""], [start], start, start + length_seconds)] \
                if request.title and request.title.strip() and title_keyword else []
        elif target == "subtitle":
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
            # 多行字效的标题整段放在第一行，其余行没有文字、不显示。
            title = data.get("segmentation", {}).get("title_en") if language == "en" else request.title
            keyword = data.get("segmentation", {}).get("title_keyword_en", "") if language == "en" else title_keyword
            if language == "en" and request.title and request.title.strip() and not (title and title.strip()):
                raise ContentError("缺少英文标题译文")
            contents = [([title], [keyword], [start], start, start + length_seconds)] if request.title and request.title.strip() else []
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
            anchor = None
            if anchoring:
                body = next((item for item in anchors[KEYWORD_TARGETS[target]] if item[1] <= first < item[2]), None)
                if body is None:
                    continue
                anchor, last = body[0], body[2]
            count = last - first
            # 多行字效的换行只用来分行：标题里自带的换行不能把标题拆到下一行的样式里。
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
                        "code": code, "config": values, "host": host, "start_frame": first,
                        "duration_frames": length, "width": sprite.canvas.width, "height": sprite.canvas.height}
            if anchor:
                # 位置与大小来自锚点里的关键词，摆放自己的宿主变换不适用。
                instance.update(anchored={"anchor": anchor, "keyword": "".join(text.split())}, host={**host, "transform": {}})
            elif language == "zh" and text_target in anchors:
                instance["anchor"] = f"{placement['id']}#{len(instances)}"
                anchors[text_target].append((instance["anchor"], first, first + length))
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
        # 计划与打包失败同样私有保存根因：公开错误只有固定摘要，打包器输出可能含资产源码。
        try:
            overlay = await asyncio.to_thread(plan, data, service.settings.overlay_max_seconds, timeline=timeline, transition_seed=task_id)
            if overlay is None:
                return None
            if environment_problem(service.settings):
                raise OverlayError("render_environment_unavailable", "Remotion 渲染环境不可用")
            publications = {key: ParseDict(value, pb.PublishedSprite()) for key, value in data["remotion"]["assets"].items()}
            async with service.overlays.slot:
                await asyncio.to_thread(shutil.rmtree, work, True)
                # 与叠加渲染相同：放不下的标题、字幕缩小字号，缩到最小仍放不下以 remotion_text_too_long 失败。
                await fit_texts(service.harness.renderer, overlay, publications, work / "fit")
                output = await render_overlay(service.harness.renderer, overlay, work)
            # 文件必须存活到上传线程收尾；取消任务不保存迟到结果。
            upload = asyncio.create_task(asyncio.to_thread(upload_public,
                [(output, f"imv/video_composition/{task_id}/overlay.webm", "video/webm")], ZosSettings(), timeout))
            try:
                urls = await asyncio.shield(upload)
            finally:
                await asyncio.gather(upload, return_exceptions=True)
            return {"video_url": urls[0], "duration": data["duration_ms"] / 1000,
                    "width": overlay.width, "height": overlay.height, "fps": overlay.fps, "frames": overlay.frames}
        except Exception as exc:
            try:
                await asyncio.to_thread(preserve_failure, work, exc)
            except OSError as diagnostic_error:
                logger.error("合成任务 %s 私有渲染诊断保存失败：%s", task_id, type(diagnostic_error).__name__)
            raise
        finally:
            await asyncio.to_thread(shutil.rmtree, work, True)
    except ContentError as exc:
        raise CompositionError("remotion_asset_not_adapted", str(exc), "assembling") from exc
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
