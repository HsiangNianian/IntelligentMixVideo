"""OpenAI 兼容模型客户端。

仅暴露"给提示词、拿 JSON 文本"这一件事，便于替换替身与离线测试。
"""

import logging

from openai import APIError, APITimeoutError, OpenAI

from ..core.errors import LLMProviderError, LLMTimeoutError

logger = logging.getLogger(__name__)


class OpenAIChatClient:
    """基于官方 openai SDK 的 OpenAI 兼容实现。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 1,
    ) -> None:
        """初始化模型客户端。

        作用与效果：保存连接配置，并按超时创建同步 SDK 客户端。
        输入：基础地址、密钥、模型名、超时秒数与 SDK 重试次数。
        输出：无。
        """
        self.model = model
        self._client = OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        """请求模型生成 JSON 正文。

        作用与效果：固定低温度与 JSON 输出格式，把 SDK 异常归一为应用异常。
        输入：系统提示与用户提示。
        输出：模型返回的正文文本。
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError() from exc
        except APIError as exc:
            logger.warning(
                "llm_request_failed",
                extra={"status_code": getattr(exc, "status_code", None)},
            )
            raise LLMProviderError(str(exc)) from exc
        return response.choices[0].message.content or ""
