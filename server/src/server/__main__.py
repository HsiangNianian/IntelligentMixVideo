"""命令行入口通过 Uvicorn 导入 FastAPI 应用，并在本机 8000 端口运行。"""

import uvicorn


def main() -> None:
    """启动 API；字符串导入路径使控制台命令与模块运行使用同一应用。"""
    uvicorn.run("server.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
