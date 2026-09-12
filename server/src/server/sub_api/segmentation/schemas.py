"""声明文案、ASR 和片段响应的数据契约，兼容词级 ASR 外层结构。"""

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class AsrWord(BaseModel):
    """ASR 词级转写单元，时间为毫秒。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    text: str
    begin_time_ms: int = Field(
        validation_alias=AliasChoices("begin_time_ms", "begin_time")
    )
    end_time_ms: int = Field(validation_alias=AliasChoices("end_time_ms", "end_time"))
    punctuation: str = ""
    confidence: float | None = None


class AsrSentence(BaseModel):
    """ASR 句级转写单元，词级时间轴可选。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    sentence_id: int | None = None
    text: str = ""
    begin_time_ms: int | None = Field(
        default=None, validation_alias=AliasChoices("begin_time_ms", "begin_time")
    )
    end_time_ms: int | None = Field(
        default=None, validation_alias=AliasChoices("end_time_ms", "end_time")
    )
    words: list[AsrWord] = Field(default_factory=list)


class AsrResult(BaseModel):
    """ASR 转写结果，兼容 fun-asr 原始响应与精简结构。"""

    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    audio_duration_ms: int | None = None
    sentences: list[AsrSentence] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def unwrap_transcript_payload(cls, value: Any) -> Any:
        """兼容 fun-asr 原始响应外层结构。

        作用与效果：把 `transcripts[0]` 提升为顶层，并补出音频总时长，不修改调用方字典。
        输入：`value` 为校验前收到的原始数据。
        输出：原数据或已解包的字典副本。
        """
        if not isinstance(value, dict) or "sentences" in value:
            return value
        transcripts = value.get("transcripts")
        if not isinstance(transcripts, list) or not transcripts:
            return value
        first = transcripts[0]
        if not isinstance(first, dict):
            return value
        merged = dict(first)
        properties = value.get("properties")
        if isinstance(properties, dict):
            duration = properties.get("original_duration_in_milliseconds")
            if isinstance(duration, int):
                merged.setdefault("audio_duration_ms", duration)
        return merged

    def iter_words(self) -> list[AsrWord]:
        """按顺序展开全部句子的词级单元。

        作用与效果：扁平化 `sentences[].words[]`，过滤空文本词，供对齐使用。
        输入：无。
        输出：按音频时间排序的词列表。
        """
        return [
            word
            for sentence in self.sentences
            for word in sentence.words
            if word.text.strip()
        ]


class SegmentKeyword(BaseModel):
    """片段关键词及其在片段文本中的字符区间 `[start, end)`。"""

    text: str
    start: int
    end: int


class Segment(BaseModel):
    """可直接用于素材召回的片段。"""

    segment_id: str
    text: str
    start_time_ms: int
    end_time_ms: int
    keywords: list[SegmentKeyword] = Field(default_factory=list)


class SegmentWarning(BaseModel):
    """片段构建过程中的非致命问题，不静默丢弃。"""

    code: str
    message: str
    detail: dict[str, Any] | None = None


class SegmentTrace(BaseModel):
    """片段构建的可复现诊断信息。"""

    matched_chars: int
    substitution_chars: int
    script_extra_chars: int
    asr_extra_chars: int
    edit_cost: int
    repair_block_count: int
    merge_count: int
    split_count: int
    segment_count: int
    keyword_rejected_count: int


class SegmentBuildResult(BaseModel):
    """片段构建接口的完整返回。"""

    segments: list[Segment]
    warnings: list[SegmentWarning] = Field(default_factory=list)
    trace: SegmentTrace


class SegmentBuildRequest(BaseModel):
    """片段构建请求。"""

    model_config = ConfigDict(extra="forbid")

    script: str = Field(
        min_length=1,
        max_length=20000,
        description="原始口播文案，提供正确文字内容",
    )
    asr_result: AsrResult = Field(description="ASR 转写结果，提供真实音频时间轴")
