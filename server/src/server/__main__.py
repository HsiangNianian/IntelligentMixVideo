"""命令行入口读取 PORT 配置，通过 Uvicorn 在所有 IPv4 接口启动 FastAPI 应用。"""

import uvicorn
from pydantic import Field

from .config_base import CommonSettings


class ServerSettings(CommonSettings):
    """读取固定的 server/.env 与优先级更高的环境变量，启动前校验端口。"""

    port: int = Field(default=20070, ge=1, le=65535)


def main() -> None:
    """启动 API；字符串导入路径使控制台命令与模块运行使用同一应用。"""
    settings = ServerSettings()
    uvicorn.run("server.app:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
