# 浏览器集成测试使用完整 FastAPI 应用与本机独立 MySQL 数据库，退出时清理本次创建的数据。
# 执行 uv run --locked --project server python server/tests/run_template_browser_server.py。
import os
import signal
from uuid import uuid4

import uvicorn
from sqlalchemy import URL, create_engine

from server.database import DatabaseSettings


def main() -> None:
    """限定本机 MySQL，以随机数据库运行真实服务，只删除本次成功创建的数据库。"""
    settings = DatabaseSettings()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("浏览器集成测试仅允许连接本机 MySQL")
    database_name = f"imv_browser_test_{uuid4().hex}"
    os.environ["DB_NAME"] = database_name
    url = URL.create(
        "mysql+pymysql", username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host, port=settings.port,
        query={"unix_socket": settings.socket} if settings.socket else {},
    )
    engine = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10})
    quoted_name = engine.dialect.identifier_preparer.quote_identifier(database_name)
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {quoted_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
    print(f"测试数据库：{database_name}", flush=True)
    try:
        from server.app import app

        @app.middleware("http")
        async def identify_test_database(request, call_next):
            """标识本次创建的测试数据库，浏览器脚本据此拒绝写入其他服务。"""
            response = await call_next(request)
            response.headers["X-IMV-Test-Database"] = database_name
            return response

        uvicorn.run(app, host="127.0.0.1", port=20171)
    finally:
        # 终端与启动器可能重复发送退出信号，清理期间让当前数据库删除操作完成。
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with engine.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted_name}")
        engine.dispose()
        print(f"已清理测试数据库：{database_name}", flush=True)


if __name__ == "__main__":
    main()
