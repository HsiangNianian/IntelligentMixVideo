"""服务监听端口定义；命令行与 Debug 内置服务共用字段及校验。"""

from pydantic import Field

from ..config_base import CommonSettings


class ServerSettings(CommonSettings):
    """读取 PORT；监听地址由启动入口决定，不开放数据库配置。"""

    port: int = Field(default=20070, ge=1, le=65535, title="监听端口")
