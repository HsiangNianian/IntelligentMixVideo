"""命令行入口读取 PORT 配置，通过 Uvicorn 在所有 IPv4 接口启动 FastAPI 应用。"""

import uvicorn
from .startup.settings import ServerSettings


def main() -> None:
    """启动 API；字符串导入路径使控制台命令与模块运行使用同一应用。

    客户端的作品 SSE 长连接不会自行结束，不限时的优雅退出会让旧进程一直持有字效
    `runtime.lock`，重启后的新进程只能返回 503；因此与桌面内置服务一样最多等待 10 秒。
    """
    settings = ServerSettings()
    uvicorn.run("server.app:app", host="0.0.0.0", port=settings.port, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
