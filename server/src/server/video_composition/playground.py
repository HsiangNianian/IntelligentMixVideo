"""读取随包封存的固定内容与中英准备结果；不依赖来源任务数据库、不调用模型或匹配服务。"""

from copy import deepcopy
import json
from pathlib import Path

from fastapi import HTTPException
from pydantic import TypeAdapter

from .schema import CompositionRequest, MatchedSegment, PlaygroundRequest, PositiveSeconds, Segment
from .timeline import validate_matches, validate_segments

# 仅放已经初始化的模式，新增模式时封存同样的中间结果，无须部署者配置任务 ID。
SAMPLES = Path(__file__).with_name("playground_samples")


def prepared_sample(payload: PlaygroundRequest) -> dict:
    """校验固定样本并返回允许复用的字段；模板与回调均由新任务决定。"""
    path = SAMPLES / f"{payload.mode}.json"
    if not path.is_file():
        raise HTTPException(409, "该模式的操练场样本尚未初始化")
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
        if source["status"] != "succeeded":
            raise ValueError("样本尚未成功")
        source_id = source["task_id"]
        data = source["data"]
        request = CompositionRequest.from_snapshot(data["request"])
        if request.composition_mode != payload.mode:
            raise ValueError("样本模式不符")
        duration = data["duration_ms"]
        if isinstance(duration, bool):
            raise ValueError("无效时长")
        TypeAdapter(PositiveSeconds).validate_python(duration)
        segmented = deepcopy(data.get("segmentation", {"segments": []}))
        if not isinstance(segmented, dict):
            raise ValueError("切片结果无效")
        segments = TypeAdapter(list[Segment]).validate_python(segmented.get("segments", []))
        if request.text:
            validate_segments(segments, duration)
            if any(not item.text_en or not item.text_en.strip() or "keyword_en" not in item.model_fields_set for item in segments):
                raise ValueError("缺少英文字幕")
        if request.title and request.title.strip():
            if not isinstance(segmented.get("title_en"), str) or not segmented["title_en"].strip():
                raise ValueError("缺少英文标题")
            if any(not isinstance(segmented.get(key), str) for key in ("title_keyword", "title_keyword_en")):
                raise ValueError("缺少标题关键词字段")
        saved_request = request.model_dump(mode="json", by_alias=True)
        if "materials" not in request.model_fields_set:
            saved_request.pop("materials")
        saved_request.update(styleId=str(payload.style_id), callbackUrl=None)
        prepared = {"request": saved_request, "duration_ms": duration, "segmentation": segmented,
                    "playground": {"mode": payload.mode, "source_task_id": str(source_id)}}
        if payload.mode == "standard":
            matches = TypeAdapter(list[MatchedSegment]).validate_python(data["matches"])
            validate_matches(segments, matches)
            prepared["matches"] = [item.model_dump(mode="json") for item in matches]
        else:
            durations = TypeAdapter(list[PositiveSeconds]).validate_python(data["material_durations"])
            if not durations or len(durations) > len(request.materials) or sum(durations) < duration / 1000:
                raise ValueError("素材时长不足")
            prepared["material_durations"] = durations
        return prepared
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "操练场样本的中英文或素材准备结果不完整，请重新初始化") from None
