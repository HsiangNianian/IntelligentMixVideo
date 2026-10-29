"""按任务冻结的模板字幕样式估算每行全角字容量，减两字后交给切片；不改模板或已发布资产。

中文字幕由 Remotion 字效接管（`text_target=subtitle`）时只按字效估算：发布默认参数合并本次摆放的覆盖，
字号乘以摆放缩放与较大的栏字号比例（关键词字号取较大者），宽度取内容区域与画布边界中较窄的一个；
旧 `businessTarget` 字效与 IMS 字幕共存，两边都参与。其余按模板中文字幕对象的字号与位置估算。
全角字按一 em，不在浏览器里实测；实际放不下时由叠加渲染的排版预检缩小字号。
"""
import json
import logging
import math
import re

from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import ParseDict

from ..remotion_templates.sprites import content_box
from ..template.schema import EffectTemplateEditor, RemotionTrackEditor
from . import remotion
from .errors import CompositionError

logger = logging.getLogger(__name__)


def ims_capacity(editor: EffectTemplateEditor, width: int) -> int:
    """居中字幕以较近画布边缘为界，按正文与关键词中较大字号预留全角字宽。"""
    available = width * 2 * min(editor.subtitle_x, 100 - editor.subtitle_x) / 100
    # shortcut: IMS 专有字体与花字在云端，按全角字一 em 估算；取得对应字体/花字度量接口后改为实测。
    size = max(editor.subtitle_size, editor.subtitle_keyword_size)
    return math.floor(available / size)


def remotion_capacity(sprite: pb.PublishedSprite, editor: RemotionTrackEditor) -> int | None:
    """字效在资产画布上的一行容量；资产没有数字 `fontSize` 时返回 None，交回 IMS 对象估算。

    覆盖值已在保存模板时校验，这里只读数值，不重复校验（字体覆盖的实时字体库检查留给渲染）。
    """
    values = {**json.loads(sprite.default_config_json or "{}"), **{item.key: item.value() for item in editor.parameters}}
    number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    font_size = values.get("fontSize")
    if not number(font_size) or font_size <= 0:
        return None
    # 双栏取较大一栏的字号比例；大于 0 的 keywordSize 是绝对字号。
    ratio = max([value for key, value in values.items() if re.fullmatch(r"line\d+SizeRatio", key) and number(value)] or [1])
    keyword_size = values["keywordSize"] if number(values.get("keywordSize")) else 0
    transform = editor.transform
    scale = transform.scale if transform and transform.scale else 1
    box = content_box(sprite)
    # 宿主变换的 x 是内容中心；内容区域宽即换行宽度，随缩放放大，仍不能超出画布。
    center = transform.x if transform and transform.x is not None else box.x + box.width / 2
    available = min(box.width * scale, 2 * min(center, sprite.canvas.width - center))
    return math.floor(available / (max(font_size * ratio, keyword_size) * scale))


def calculate(task_id: str, data: dict) -> int:
    """每单计算一次并由调用方保存：中文字幕的最小容量减两字（至少 1），没有中文字幕时用默认 8。

    纯素材有声模式没有字幕对象时按默认字幕样式估算，与 timeline.build 一致；一字也放不下时在 template 阶段失败。
    摆放用途已由 `remotion.require_environment` 在此前校验。
    """
    editors = [EffectTemplateEditor.model_validate(track["editor"]) for track in data["template"].get("tracks", [])
               if track["target"] == "subtitle" and track.get("language", "zh") == "zh"]
    if not editors and data["request"].get("compositionMode") == "materials_voice":
        editors = [EffectTemplateEditor()]
    effects, replaced = [], False
    for placement in remotion.active_placements(data):
        sprite = ParseDict(data["remotion"]["assets"][placement["sprite_id"]], pb.PublishedSprite())
        if placement.get("language", "zh") != "zh" or remotion.placement_target(placement, sprite) != "subtitle":
            continue
        capacity = remotion_capacity(sprite, RemotionTrackEditor.model_validate(placement.get("editor", {})))
        if capacity is not None:
            effects.append(capacity)
            # 新槽位替代 IMS 中文字幕；旧 businessTarget 两套字幕同时显示。
            replaced = replaced or bool(placement.get("text_target"))
    capacities = ([] if replaced else [ims_capacity(editor, data["output"]["width"]) for editor in editors]) + effects
    if capacities and min(capacities) < 1:
        raise CompositionError("subtitle_capacity_too_small", "当前字幕样式一行放不下一个全角字，请调整字号或位置", "template")
    limit = max(1, min(capacities) - 2) if capacities else 8
    logger.info("Subtitle capacity task_id=%s full_width_chars=%s max_segment_chars=%s", task_id, capacities, limit)
    return limit
