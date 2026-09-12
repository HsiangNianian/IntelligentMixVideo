"""定义 API 错误码、HTTP 状态和公开消息，供统一异常处理器生成响应。"""

from collections.abc import Sequence
from typing import Any


class AppError(Exception):
    """应用错误基类，携带对外状态码、错误详情与可重试标记。"""

    code = "application_error"
    status_code = 500
    default_message = "请求处理失败。"
    retryable = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Sequence[dict[str, Any]] | None = None,
        retryable: bool | None = None,
    ) -> None:
        """构造包含 API 元数据的应用异常。

        作用与效果：解析默认消息、结构化详情和可重试标志，并初始化异常文本。
        输入：可选消息、详情列表及可选的重试策略覆盖值。
        输出：无；异常实例获得统一的错误响应属性。
        """
        self.message = message or self.default_message
        self.details = list(details) if details else None
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)


class RequestInvalidError(AppError):
    """请求参数校验失败，转换为 HTTP 422。"""

    code = "request_invalid"
    status_code = 422
    default_message = "请求参数无效。"


class AsrTimelineMissingError(RequestInvalidError):
    """ASR 缺少词级时间轴，拒绝进行时间投射。"""

    code = "asr_timeline_missing"
    default_message = "ASR 结果缺少词级时间戳，无法建立时间轴。"


class AsrTranscriptTooLongError(RequestInvalidError):
    """ASR 字符或词数超限，在展开阶段拒绝输入。"""

    code = "asr_transcript_too_long"
    default_message = "ASR 转写文本超出可对齐长度上限。"


class AlignmentInputTooLargeError(RequestInvalidError):
    """对齐工作预算耗尽，终止搜索并返回 422。"""

    code = "alignment_input_too_large"
    default_message = "文案与 ASR 文本的对齐计算超出工作预算。"


class ScriptAlignmentError(AppError):
    """文案与 ASR 无法可靠匹配，拒绝生成片段。"""

    code = "script_asr_alignment_failed"
    status_code = 422
    default_message = "文案与 ASR 时间轴无法建立可靠对齐。"


class SegmentInvariantError(AppError):
    """生成片段违反内部约束，报告服务端错误。"""

    code = "segment_invariant_violated"
    status_code = 500
    default_message = "片段构建结果未通过内部一致性校验。"


class LLMError(AppError):
    """模型调用失败的公共错误类型。"""

    code = "llm_provider_error"
    status_code = 502
    default_message = "模型服务调用失败。"


class LLMTimeoutError(LLMError):
    """模型请求超时，返回可重试的 HTTP 504。"""

    code = "llm_timeout"
    status_code = 504
    default_message = "模型服务响应超时，请稍后重试。"
    retryable = True


class LLMProviderError(LLMError):
    """模型服务不可用或请求失败，返回 HTTP 502。"""

    code = "llm_provider_error"
    status_code = 502
    default_message = "模型服务暂时不可用。"


class LLMOutputInvalidError(LLMError):
    """模型返回结构不合法，不重试解析失败的输出。"""

    code = "llm_output_invalid"
    status_code = 502
    default_message = "模型输出无法通过结构校验。"
