"""按任务冻结的字幕样式计算每行全角字容量，减两字后交给切片；不改模板或已发布资产。

IMS 按居中位置可用宽度与全角 em 字宽估算；Remotion 在既有隔离浏览器里测量真实字体、换行和宿主变换。
"""
import asyncio
import hashlib
import json
import logging
import math
import shutil

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import ParseDict

from ..remotion_templates.categories import LINE_STARTS_PROP, line_count
from ..remotion_templates.fonts import FontStore, open_font_schema
from ..remotion_templates.overlay_render import overlay_cpu_seconds
from ..remotion_templates.placement import resolve
from ..remotion_templates.render_resources import RenderCapacityTimeout
from ..remotion_templates.tool_validation import FitSprite, ToolValidator
from ..remotion_templates.tools.contracts import ComponentDefinition
from ..template.schema import EffectTemplateEditor, RemotionTrackEditor
from . import remotion
from .errors import CompositionError
from .remotion_content import fields, roles, set_path

logger = logging.getLogger(__name__)


def ims_capacity(editor: EffectTemplateEditor, width: int) -> int:
    """居中字幕以较近画布边缘为界，按正文与关键词中较大字号预留全角字宽。"""
    available = width * 2 * min(editor.subtitle_x, 100 - editor.subtitle_x) / 100
    # shortcut: IMS 专有字体与花字在云端，按全角字一 em 估算；取得对应字体/花字度量接口后改为实测。
    size = max(editor.subtitle_size, editor.subtitle_keyword_size)
    return math.floor(available / size)


def measurement_inputs(data: dict) -> tuple[list[int], list[FitSprite]]:
    """只读取会显示中文切片的字幕，复用发布参数覆盖和旧用途解析；双栏共用最小单行容量。"""
    capacities, probes = [], []
    replacements = False
    for placement in remotion.active_placements(data):
        if placement.get("language", "zh") != "zh":
            continue
        sprite = ParseDict(data["remotion"]["assets"][placement["sprite_id"]], pb.PublishedSprite())
        if remotion.placement_target(placement, sprite) != "subtitle":
            continue
        replacements |= placement.get("text_target") == "subtitle"
        if hashlib.sha256(sprite.tsx_code.encode()).hexdigest() != sprite.code_sha256:
            raise ValueError("Remotion 资产快照源码校验失败")
        values, host = resolve(sprite, RemotionTrackEditor.model_validate(placement.get("editor", {})))
        schema = open_font_schema(json.loads(sprite.config_schema_json))
        mapping, definitions = roles(schema), dict(fields(schema))
        paths = list(dict.fromkeys([*mapping["text"], *([sprite.text_prop] if sprite.text_prop else [])]))
        if not paths or any(definitions.get(path, {}).get("type") != "string" for path in paths):
            raise ValueError("字幕资产缺少可注入的正文参数")
        lines = line_count(schema)
        maximum = min(definitions[path].get("maxLength", sprite.canvas.width + sprite.canvas.height) for path in paths)
        # 容量探针清除示例关键词，各栏从首帧同时占位；实际内容仍由合成的排版预检兜底。
        for path in dict.fromkeys([*mapping["keywords"], *([sprite.keywords_prop] if sprite.keywords_prop else [])]):
            set_path(values, path, "" if definitions[path].get("type") == "string" else [])
        if LINE_STARTS_PROP in schema.get("properties", {}):
            values[LINE_STARTS_PROP] = [0] * lines
        probes.append(FitSprite(
            ComponentDefinition(code=sprite.tsx_code, parameter_schema=schema, default_parameters=values),
            {"width": sprite.canvas.width, "height": sprite.canvas.height}, host["box"],
            [{"values": values, "text_paths": paths, "lines": lines,
              "max_chars": max(0, (maximum - lines + 1) // lines),
              "length": sprite.canvas.preview_frames, "transform": host["transform"]}],
        ))
    if not replacements:
        editors = [EffectTemplateEditor.model_validate(track["editor"]) for track in data["template"].get("tracks", [])
                   if track["target"] == "subtitle" and track.get("language", "zh") == "zh"]
        # 纯素材模式会在没有字幕对象时使用默认字幕样式，与 timeline.build 保持一致。
        if not editors and data["request"].get("compositionMode") == "materials_voice":
            editors = [EffectTemplateEditor()]
        capacities.extend(ims_capacity(editor, data["output"]["width"]) for editor in editors)
    return capacities, probes


async def calculate(task_id: str, data: dict) -> int:
    """每单计算一次并由调用方保存；测量占共享渲染名额，字体或环境失败不伪造容量。"""
    try:
        capacities, probes = await asyncio.to_thread(measurement_inputs, data)
        if probes:
            service = await remotion.sprite_runtime()
            work = service.store.root / "composition-capacities" / task_id
            try:
                store = FontStore.active
                if store is not None:
                    named = store.named(json.dumps([probe.component.model_dump() for probe in probes], ensure_ascii=False))
                    if named and await store.require(named):
                        raise RuntimeError("字幕需要的字体尚未就绪")
                timeout = max(service.settings.render_timeout_seconds, 30 * len(probes))
                async with service.overlays.slot, service.overlays.capacity.claim(1, 1):
                    measured = await ToolValidator(service.harness.renderer, work).line_capacities(
                        probes, {**{key: data["output"][key] for key in ("width", "height", "fps")},
                                 "duration_frames": max(probe.items[0]["length"] for probe in probes)},
                        timeout_seconds=timeout, cpu_seconds=overlay_cpu_seconds(timeout),
                    )
                capacities.extend(value for values in measured for value in values)
            finally:
                await asyncio.to_thread(shutil.rmtree, work, True)
        if capacities and min(capacities) < 1:
            raise CompositionError("subtitle_capacity_too_small", "当前字幕样式一行放不下一个全角字，请调整字号、位置或缩放", "template")
        limit = max(1, min(capacities) - 2) if capacities else 8
        logger.info("Subtitle capacity task_id=%s full_width_chars=%s max_segment_chars=%s", task_id, capacities, limit)
        return limit
    except CompositionError:
        raise
    except RenderCapacityTimeout as exc:
        raise CompositionError("remotion_resource_unavailable", str(exc), "template") from exc
    except Exception as exc:
        logger.exception("Subtitle capacity measurement failed task_id=%s", task_id)
        raise CompositionError("subtitle_capacity_failed", "无法计算字幕每行字数，请检查字幕资产及渲染环境后重试", "template") from exc
