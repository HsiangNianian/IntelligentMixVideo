"""统一源码运行时的 server/.env 读取规则；字段与实例化时机仍由各模块维护。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class CommonSettings(BaseSettings):
    """复用默认配置源优先级，忽略其他模块字段并隐藏校验错误中的输入。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
