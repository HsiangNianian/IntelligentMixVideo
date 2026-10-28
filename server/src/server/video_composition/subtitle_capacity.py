"""按任务冻结的模板字幕样式估算每行全角字容量，减两字后交给切片；不改模板或已发布资产。

只用模板中文字幕对象的字号与位置估算：居中位置到较近画布边缘的两倍宽度，除以正文与关键词中较大的字号
（全角字按一 em）。Remotion 字效替代字幕时同样按该对象估算，不在浏览器里实测；实际放不下时由叠加渲染的排版预检缩小字号。
"""
import logging
import math

from ..template.schema import EffectTemplateEditor
from .errors import CompositionError

logger = logging.getLogger(__name__)


def ims_capacity(editor: EffectTemplateEditor, width: int) -> int:
    """居中字幕以较近画布边缘为界，按正文与关键词中较大字号预留全角字宽。"""
    available = width * 2 * min(editor.subtitle_x, 100 - editor.subtitle_x) / 100
    # shortcut: IMS 专有字体与花字在云端，按全角字一 em 估算；取得对应字体/花字度量接口后改为实测。
    size = max(editor.subtitle_size, editor.subtitle_keyword_size)
    return math.floor(available / size)


def calculate(task_id: str, data: dict) -> int:
    """每单计算一次并由调用方保存：中文字幕对象的最小容量减两字（至少 1），没有中文字幕对象时用默认 8。

    纯素材有声模式没有字幕对象时按默认字幕样式估算，与 timeline.build 一致；一字也放不下时在 template 阶段失败。
    """
    editors = [EffectTemplateEditor.model_validate(track["editor"]) for track in data["template"].get("tracks", [])
               if track["target"] == "subtitle" and track.get("language", "zh") == "zh"]
    if not editors and data["request"].get("compositionMode") == "materials_voice":
        editors = [EffectTemplateEditor()]
    capacities = [ims_capacity(editor, data["output"]["width"]) for editor in editors]
    if capacities and min(capacities) < 1:
        raise CompositionError("subtitle_capacity_too_small", "当前字幕样式一行放不下一个全角字，请调整字号或位置", "template")
    limit = max(1, min(capacities) - 2) if capacities else 8
    logger.info("Subtitle capacity task_id=%s full_width_chars=%s max_segment_chars=%s", task_id, capacities, limit)
    return limit
