"""合成请求、状态和业务快照校验；URL 只校验，不改写签名查询参数。"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationInfo, model_validator,
)
from pydantic.alias_generators import to_camel
from pydantic.json_schema import SkipJsonSchema


def media_url(value: str) -> str:
    """接受无用户信息的 HTTP(S) 直链，保留调用方原始 URL。"""
    parsed = TypeAdapter(HttpUrl).validate_python(value)
    if value != value.strip() or any(c.isspace() for c in value) or parsed.username is not None or parsed.password is not None or parsed.fragment is not None:
        raise ValueError("媒体必须为不含空白、用户信息或片段的 HTTP(S) 直链")
    return value


MediaURL = Annotated[str, AfterValidator(media_url)]
PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Status = Literal["queued", "processing", "succeeded", "failed"]
Stage = Literal[
    "queued", "template", "asr", "segmentation", "translation", "matching", "assembling", "submitting",
    "rendering", "uploading", "completed", "failed",
]


class APIModel(BaseModel):
    """HTTP 模型统一使用 camelCase；兼容字段接收但不参与合成。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Material(APIModel):
    """标准模式的匹配候选或纯素材模式的顺序画面，类型由调用方明确声明。"""

    file_url: MediaURL
    type: Literal["video", "image"]


class BackgroundMusic(APIModel):
    """音乐关闭时不校验或访问地址；开启时必须提供有效 URL 和有限增益。"""

    audio_switch: bool = False
    audio_url: str | None = None
    volume: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def enabled_url(self) -> Self:
        """仅启用音乐时要求可提交给 IMS 的媒体地址。"""
        if self.audio_switch:
            media_url(self.audio_url or "")
        return self


class PackRules(APIModel):
    """仅背景音乐生效，null 表示关闭；其余包装开关被忽略。"""

    background_music: BackgroundMusic | None = Field(default_factory=BackgroundMusic)


class ProcessRules(APIModel):
    """无语音纯素材使用秒制成片时长，其余字段不参与合成。"""

    video_duration: PositiveSeconds | None = Field(default=None, strict=True)


class CompositionRequest(APIModel):
    """合成输入含可选终态通知地址；原声音量、水印、卡片和其他控制字段暂不生效。"""

    text: str | None = Field(default=None, max_length=20000)
    copy_text: str | None = Field(default=None, alias="copy", max_length=20000, exclude=True)
    composition_mode: SkipJsonSchema[Literal["standard", "materials_voice", "materials_silent"]] = "materials_silent"
    video_url: MediaURL | None = None
    audio_url: MediaURL | None = None
    style_id: UUID
    title: str | None = None
    materials: list[Material] = Field(default_factory=list, description="standard 省略为纯数字人、显式数组匹配；纯素材要求非空数组并按序使用")
    pack_rules: PackRules = Field(default_factory=PackRules)
    process_rules: ProcessRules = Field(default_factory=ProcessRules)
    callback_url: MediaURL | None = None

    @classmethod
    def from_snapshot(cls, value: dict) -> Self:
        """内部恢复读取已存模式；旧快照仍按原视频地址规则解释，不能使用新请求规则。"""
        return cls.model_validate(value, context={"snapshot": True})

    @model_validator(mode="before")
    @classmethod
    def resolve_mode_and_copy(cls, value, info: ValidationInfo):
        """新请求先按文案来源判定模式再合并正文；调用方不能指定内部模式。"""
        if isinstance(value, dict):
            value = value.copy()
            text, copy = (None if isinstance(item, str) and not item.strip() else item
                          for item in (value.get("text"), value.get("copy", value.get("copy_text"))))
            audio = value.get("audioUrl", value.get("audio_url"))
            mode = "materials_voice" if audio is not None else "materials_silent"
            if info.context and info.context.get("snapshot"):
                if value.get("videoUrl", value.get("video_url")) is not None:
                    mode = "standard"
                mode = value.get("compositionMode", value.get("composition_mode", mode))
            else:
                if text is not None and copy is not None:
                    raise ValueError("text 和 copy 不能同时非空")
                if (text is not None or copy is not None) and audio is None:
                    raise ValueError("提供口播文案时必须提供 audioUrl")
                if text is not None:
                    mode = "standard"
            value.update(text=text if text is not None else copy, copy=copy, compositionMode=mode)
        return value

    @model_validator(mode="after")
    def mode_fields(self) -> Self:
        """按模式检查真实依赖；纯素材不需要数字人视频，无语音不需要配音或文案。"""
        if self.composition_mode == "standard":
            if not self.text or not self.video_url:
                raise ValueError("standard 模式需要文案和 videoUrl")
        elif not self.materials:
            raise ValueError("纯素材模式需要非空 materials")
        if self.composition_mode == "materials_silent":
            if self.process_rules.video_duration is None:
                raise ValueError("无语音模式需要 processRules.videoDuration")
        elif not self.audio_url or not self.audio_url.startswith("https://"):
            raise ValueError("audioUrl 必须是 HTTPS 直链")
        return self


class AcceptedResponse(APIModel):
    """创建接口返回操作结果和本地任务标识，查询接口保持独立响应结构。"""

    code: Literal[200] = 200
    message: Literal["操作成功"] = "操作成功"
    data: UUID


class PlaygroundRequest(APIModel):
    """操练场只选择固定样本与模板，正文和媒体不能在此覆盖。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")
    mode: Literal["standard", "materials_voice", "materials_silent"]
    style_id: UUID


class Result(APIModel):
    """成片实际秒数与新任务的 ZOS 地址；历史记录仍可返回 IMS 临时地址。"""

    video_url: MediaURL
    duration_seconds: PositiveSeconds


class TaskError(BaseModel):
    """可公开的固定错误摘要，不包含供应商响应、输入地址或凭证。"""

    code: str
    message: str
    stage: str


class TaskResponse(APIModel):
    """查询仅暴露任务状态，不暴露输入或供应商内部快照。"""

    task_id: UUID
    status: Status
    stage: Stage
    result: Result | None = None
    error: TaskError | None = None
    created_at: datetime
    updated_at: datetime


class SubtitlePart(BaseModel):
    """切片内部保留问号的字幕文字及连续秒制区间；不发送给素材匹配。"""

    text: str = Field(min_length=1)
    start_time: float = Field(ge=0, allow_inf_nan=False, strict=True)
    end_time: PositiveSeconds = Field(strict=True)


class Segment(BaseModel):
    """切片保留素材匹配字段；字幕短句只供合成使用，序列化匹配请求时排除。"""

    segment_id: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1)
    start_time: float = Field(ge=0, allow_inf_nan=False, strict=True)
    end_time: PositiveSeconds = Field(strict=True)
    keyword: str
    level: int = Field(ge=1, le=2, strict=True)
    group_id: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=2, max_length=2)
    # 英文仅供渲染，序列化素材匹配请求时排除。
    text_en: str | None = Field(default=None, exclude=True)
    keyword_en: str = Field(default="", exclude=True)
    subtitle_parts: list[SubtitlePart] | None = Field(default=None, min_length=1, exclude=True)

    @model_validator(mode="after")
    def valid_segment(self) -> Self:
        """校验句内序号，并保证字幕短句覆盖原片段且时间首尾衔接。"""
        if self.group_id[0] > self.group_id[1]:
            raise ValueError("片段组内序号不能超过总数")
        if self.subtitle_parts and (
            self.subtitle_parts[0].start_time != self.start_time
            or self.subtitle_parts[-1].end_time != self.end_time
            or any(left.end_time != right.start_time for left, right in zip(self.subtitle_parts, self.subtitle_parts[1:]))
            or any(part.start_time >= part.end_time for part in self.subtitle_parts)
        ):
            raise ValueError("字幕短句时间必须在原切片内首尾衔接")
        return self


class MatchedSegment(BaseModel):
    """匹配业务片段的已知字段；无 URL 表示未命中，命中必须声明媒体类型。"""

    segment_id: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1)
    start_time: float = Field(ge=0, allow_inf_nan=False)
    end_time: PositiveSeconds
    matched_candidate_url: MediaURL | None = None
    matched_candidate_type: Literal["video", "image"] | None = None

    @model_validator(mode="after")
    def matched_type(self) -> Self:
        """不通过扩展名或匹配分数猜媒体类型。"""
        if self.matched_candidate_url and self.matched_candidate_type is None:
            raise ValueError("命中素材缺少类型")
        return self


class MatchResult(BaseModel):
    """匹配成功业务对象；额外的供应商字段不进入任务快照。"""

    segments: list[MatchedSegment] = Field(min_length=1)


class MatchCallback(BaseModel):
    """素材服务回调使用上游 taskId；成功必须提供可校验的片段结果。"""

    taskId: str = Field(min_length=1, max_length=200)
    status: Literal["success", "failed"]
    result: MatchResult | None = None

    @model_validator(mode="after")
    def success_result(self) -> Self:
        """失败仅记录固定摘要，成功不能用空对象代替匹配结果。"""
        if self.status == "success" and self.result is None:
            raise ValueError("匹配成功回调缺少 result")
        return self
