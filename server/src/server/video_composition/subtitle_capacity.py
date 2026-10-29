"""在 ASR 前确定字幕切片上限：IMS 按一 em 估算，Remotion 用封存字效的浏览器排版实测全角样本。

仅测量预览时序下的容量，不缩字、不移动摆放、不改资产。实际文案、关键词和时序仍由成片排版预检核验。
"""
import asyncio
import hashlib
import json
import logging
import math
import shutil

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import ParseDict

from ..remotion_templates.fonts import open_font_schema
from ..remotion_templates.overlay_render import Overlay, overlay_cpu_seconds, require_fonts
from ..remotion_templates.placement import resolve
from ..remotion_templates.tool_validation import FitSprite, ToolValidator
from ..remotion_templates.tools.contracts import ComponentDefinition
from ..template.schema import EffectTemplateEditor, RemotionTrackEditor
from . import remotion
from .errors import CompositionError
from .remotion_content import capacity_samples

logger = logging.getLogger(__name__)


def ims_capacity(editor: EffectTemplateEditor, width: int) -> int:
    """居中字幕以较近画布边缘为界，按正文与关键词中较大字号预留全角字宽。"""
    available = width * 2 * min(editor.subtitle_x, 100 - editor.subtitle_x) / 100
    # IMS 专有字体与花字在云端，按全角字一 em 估算；取得对应字体/花字度量接口后改为实测。
    size = max(editor.subtitle_size, editor.subtitle_keyword_size)
    return math.floor(available / size)


def probes(placements: list[tuple[dict, pb.PublishedSprite]]) -> list[FitSprite]:
    """用渲染相同的参数解析器构造样本；合并同一资产的摆放，一次浏览器加载即可测量所有覆盖。"""
    groups: dict[str, FitSprite] = {}
    for index, (placement, sprite) in enumerate(placements):
        if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
            raise ValueError("Remotion 资产快照源码校验失败")
        config, host = resolve(sprite, RemotionTrackEditor.model_validate(placement.get("editor", {})))
        length = sprite.canvas.preview_frames
        lines, samples = capacity_samples(sprite, config, length)
        if sprite.sprite_id not in groups:
            groups[sprite.sprite_id] = FitSprite(
                ComponentDefinition(code=sprite.tsx_code, parameter_schema=open_font_schema(json.loads(sprite.config_schema_json)),
                                    default_parameters=json.loads(sprite.default_config_json)),
                {"width": sprite.canvas.width, "height": sprite.canvas.height, "fps": sprite.canvas.fps}, host["box"], [],
            )
        groups[sprite.sprite_id].items.append({
            "index": index, "samples": samples, "lines": lines, "length": length,
            "frames": sorted({math.floor((length - 1) * ratio) for ratio in (0, 0.25, 0.5, 0.75, 1)}),
            "transform": host["transform"],
        })
    return list(groups.values())


async def remotion_capacities(task_id: str, data: dict, placements: list[tuple[dict, pb.PublishedSprite]]) -> list[int]:
    """共用渲染准入、字体准备与隔离 worker；失败不回退隐藏的 IMS 字号，取消/失败均清理临时文件。"""
    work = None
    try:
        service = await remotion.sprite_runtime()
        work = service.store.root / "subtitle-capacity" / task_id
        items = await asyncio.to_thread(probes, placements)
        output = data["output"]
        length = max(item["length"] for sprite in items for item in sprite.items)
        # 字体准备读取原始源码与合并后的参数，与成片路径相同，避免测到回退字体。
        overlay = Overlay(output["width"], output["height"], output["fps"], length,
                          [{"code": sprite.component.code, "config": item["samples"][-1]["values"][0]}
                           for sprite in items for item in sprite.items])
        timeout = max(service.settings.render_timeout_seconds, 10 * len(placements) + 10 * len(items))
        async with service.overlays.slot, service.overlays.capacity.claim(1, 1):
            await require_fonts(overlay)
            answers = await ToolValidator(service.harness.renderer, work).text_capacities(
                items, {"width": overlay.width, "height": overlay.height, "fps": overlay.fps, "duration_frames": length},
                timeout_seconds=timeout, cpu_seconds=overlay_cpu_seconds(timeout),
            )
        return [answer["capacity"] for group in answers for answer in group]
    except Exception as exc:
        try:
            if work is not None:
                await asyncio.to_thread(remotion.preserve_failure, work, exc)
        except OSError:
            logger.warning("字幕容量测量私有诊断保存失败 task_id=%s", task_id)
        raise CompositionError("subtitle_capacity_unavailable", "Remotion 字幕尺寸测量失败，请检查字效或稍后重试", "template") from exc
    finally:
        if work is not None:
            await asyncio.to_thread(shutil.rmtree, work, True)


async def calculate(task_id: str, data: dict) -> int:
    """本单最小单行全角容量减二（至少一字），没有中文字幕时默认八字；恢复由调用方复用已存结果。

    新槽位替代隐藏的 IMS 字幕；旧 businessTarget 两者共存取较小容量。无数字字号的字效也实测。
    纯素材有声模式没有字幕对象时使用默认 IMS 样式；一字放不下时在付费 ASR/切片之前失败。
    """
    editors = [EffectTemplateEditor.model_validate(track["editor"]) for track in data["template"].get("tracks", [])
               if track["target"] == "subtitle" and track.get("language", "zh") == "zh"]
    if not editors and data["request"].get("compositionMode") == "materials_voice":
        editors = [EffectTemplateEditor()]
    placements = []
    for placement in remotion.active_placements(data):
        if placement.get("language", "zh") != "zh":
            continue
        sprite = ParseDict(data["remotion"]["assets"][placement["sprite_id"]], pb.PublishedSprite())
        if remotion.placement_target(placement, sprite) == "subtitle":
            placements.append((placement, sprite))
    replaced = any(placement.get("text_target") for placement, _ in placements)
    capacities = [] if replaced else [ims_capacity(editor, data["output"]["width"]) for editor in editors]
    if placements:
        capacities.extend(await remotion_capacities(task_id, data, placements))
    if capacities and min(capacities) < 1:
        raise CompositionError("subtitle_capacity_too_small", "当前字幕样式一行放不下一个全角字，请调整字号或位置", "template")
    limit = max(1, min(capacities) - 2) if capacities else 8
    logger.info("Subtitle capacity task_id=%s full_width_chars=%s max_segment_chars=%s", task_id, capacities, limit)
    return limit
