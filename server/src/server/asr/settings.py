"""ASR 配置字段声明；读取规则由 CommonSettings 提供，导入本文件不实例化配置。"""

from pydantic import Field, SecretStr

from ..config_base import CommonSettings


class ASRSettings(CommonSettings):
    """从固定的 server/.env 读取 DashScope 密钥，进程环境变量优先。"""

    dashscope_api_key: SecretStr = Field(default=SecretStr(""), title="DashScope API Key")
