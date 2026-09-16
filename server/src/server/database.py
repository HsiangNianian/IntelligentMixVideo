"""通过 pydantic-settings 读取 MySQL 配置；启动时确保数据库存在，退出时释放连接池。"""

import json
from threading import Lock

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.exc import ArgumentError, OperationalError

from .config_base import CommonSettings


class DatabaseSettings(CommonSettings):
    """自动读取 DB_* 环境变量及固定的 server/.env，环境变量优先，不修改进程环境。"""

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "127.0.0.1"
    port: int = Field(default=3306, ge=1, le=65535)
    user: str = "root"
    password: SecretStr = SecretStr("")
    name: str = Field(default="intelligent_mix_video", min_length=1, max_length=64)


# 连接池与配置在首次使用时一起初始化；退出后可重新读取配置。
_engine: Engine | None = None
_engine_lock = Lock()
# 目标库与临时建库连接共用有界超时，避免启动或请求无限等待。
_connection_timeouts = {"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10}


def get_engine() -> Engine:
    """创建或复用目标库连接池；启动初始化和模板请求共用已校验的配置。"""
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                settings = DatabaseSettings()
            except ValidationError:
                # 复用数据库错误响应，返回可重试的 503，不暴露配置输入。
                raise ArgumentError("MySQL 配置无效，请检查 server/.env") from None
            url = URL.create(
                "mysql+pymysql", username=settings.user,
                password=settings.password.get_secret_value(),
                host=settings.host, port=settings.port, database=settings.name,
                query={"charset": "utf8mb4"},
            )
            _engine = create_engine(
                url, pool_pre_ping=True, pool_recycle=1800,
                json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
                connect_args=_connection_timeouts,
            )
        return _engine


def initialize_database() -> None:
    """启动时检查目标库，仅在不存在时创建；并发启动幂等，失败向上传递并释放临时连接。"""
    engine = get_engine()
    try:
        with engine.connect():
            return
    except OperationalError as exc:
        # 仅处理 MySQL 1049（未知数据库），鉴权、权限或网络错误不能触发建库。
        if not exc.orig.args or exc.orig.args[0] != 1049:
            raise

    bootstrap = create_engine(
        engine.url._replace(database=None),
        isolation_level="AUTOCOMMIT", connect_args=_connection_timeouts,
    )
    try:
        # 库名属于 SQL 标识符，使用方言引用以正确处理保留字与反引号。
        name = engine.dialect.identifier_preparer.quote_identifier(engine.url.database)
        with bootstrap.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE IF NOT EXISTS {name} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
    finally:
        bootstrap.dispose()
    # 建库后再次验证目标库可连接，再允许应用完成启动。
    with engine.connect():
        pass


def close_database() -> None:
    """应用退出时释放连接池，避免重启或多次加载保留连接。"""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
