from collections.abc import Sequence
from typing import Any


class AppError(Exception):
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
    code = "request_invalid"
    status_code = 422
    default_message = "请求参数无效。"


class AsrTimelineMissingError(RequestInvalidError):
    code = "asr_timeline_missing"
    default_message = "ASR 结果缺少词级时间戳，无法建立时间轴。"


class ScriptAlignmentError(AppError):
    code = "script_asr_alignment_failed"
    status_code = 422
    default_message = "文案与 ASR 时间轴无法建立可靠对齐。"


class SegmentInvariantError(AppError):
    code = "segment_invariant_violated"
    status_code = 500
    default_message = "片段构建结果未通过内部一致性校验。"


class LLMError(AppError):
    code = "llm_provider_error"
    status_code = 502
    default_message = "模型服务调用失败。"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Sequence[dict[str, Any]] | None = None,
        retryable: bool | None = None,
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        """构造带内部诊断数据的模型服务异常。

        作用与效果：复制诊断字典以隔离外部修改，并沿用统一应用错误属性。
        输入：可选消息、公开详情、重试标志及仅供日志使用的诊断信息。
        输出：无；异常实例保存独立的诊断副本。
        """
        self.diagnostic = dict(diagnostic or {})
        super().__init__(message, details=details, retryable=retryable)


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    status_code = 504
    default_message = "模型服务响应超时，请稍后重试。"
    retryable = True


class LLMProviderError(LLMError):
    code = "llm_provider_error"
    status_code = 502
    default_message = "模型服务暂时不可用。"


class LLMOutputInvalidError(LLMError):
    code = "llm_output_invalid"
    status_code = 502
    default_message = "模型输出无法通过结构校验。"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        """构造不可重试的模型输出校验异常。

        作用与效果：固定 `retryable=False`，避免对确定性的结构错误执行无意义重试。
        输入：可选错误消息和结构化校验详情。
        输出：无；异常实例被标记为不可重试。
        """
        super().__init__(message, details=details, retryable=False)
