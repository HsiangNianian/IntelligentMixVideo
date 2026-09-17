"""测试包入口：私有 MySQL → 回环 API → 就绪回执；桌面退出后收束服务。

仅由 IMV_DEBUG 客户端启动，运行时为只读资源，数据及 .env 留在用户目录。
标准输入关闭即代表桌面退出；标准输出只发送一行就绪 JSON，诊断写入 stderr。
"""

import asyncio
import json
import os
from pathlib import Path
import secrets
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pymysql
import uvicorn
from dotenv import load_dotenv

from .file_lock import lock_exclusive

WINDOWS = sys.platform == "win32"
# Windows 工具始终写入诊断管道，双击客户端不弹出独立控制台。
CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if WINDOWS else 0


def configure(runtime: Path, data: Path, mysql_socket: Path) -> None:
    """创建无密钥配置样例；固定内置工具、私有数据库和持久化目录，不覆盖用户模型密钥。"""
    config = data / ".env"
    if not config.exists():
        config.write_bytes((runtime / ".env.example").read_bytes())
        config.chmod(0o600)
    load_dotenv(config, override=False)
    manifest = runtime / "runtime.json"
    browser = json.loads(manifest.read_text())["browser"] if manifest.exists() else "chrome/chrome"
    os.environ.update({
        "DB_HOST": "localhost", "DB_PORT": "3306", "DB_USER": "root",
        "DB_PASSWORD": "", "DB_NAME": "intelligent_mix_video",
        "DB_SOCKET": str(mysql_socket),
        "IMV_DATA_DIR": str(data / "remotion"),
        "IMV_RENDERER_DIR": str(runtime / "renderer"),
        "IMV_BROWSER_EXECUTABLE": str(runtime / browser),
        "IMV_FONT_REGULAR": str(runtime / "fonts" / "NotoSansCJK-Regular.ttc"),
        "IMV_FONT_BOLD": str(runtime / "fonts" / "NotoSansCJK-Bold.ttc"),
        "IMV_RUNTIME_LIB_DIR": str(runtime / "lib"),
        "PATH": os.pathsep.join([str(runtime / "bin"), str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")])
        if WINDOWS else str(runtime / "bin") + ":/usr/bin:/bin",
    })
    if WINDOWS:
        # Windows 的 PyMySQL 不支持 Unix socket；只使用带随机密码的动态回环端口。
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        os.environ.update(DB_HOST="127.0.0.1", DB_PORT=str(port), DB_SOCKET="", DB_PASSWORD=secrets.token_hex(32))
    os.chdir(data)


def stop(process: subprocess.Popen) -> None:
    """先正常终止并等待落盘，超时才强制回收子进程。"""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def mysql_command(runtime: Path, directory: Path, mysql_socket: Path) -> list[str]:
    """忽略宿主配置；Unix 使用私有 socket，Windows 使用有密码的回环 TCP。"""
    command = [
        str(runtime / "bin" / ("mysqld.exe" if WINDOWS else "mysqld")), "--no-defaults",
        f"--basedir={runtime}", f"--datadir={directory}",
        f"--lc-messages-dir={runtime / 'share' / 'mysql'}",
        f"--plugin-dir={runtime / 'mysql-plugins'}",
        f"--socket={mysql_socket}", f"--pid-file={mysql_socket.parent / 'mysql.pid'}",
        "--mysqlx=OFF", "--skip-log-bin",
        "--secure-file-priv=NULL", "--innodb-buffer-pool-size=64M",
    ]
    if WINDOWS:
        command.extend(["--bind-address=127.0.0.1", f"--port={os.environ['DB_PORT']}", "--console"])
    else:
        command.append("--skip-networking")
    return command


def mysql_connection(mysql_socket: Path):
    """连接本次启动的私有数据库，限定连接和读写超时；绝不回退宿主默认配置。"""
    address = {"unix_socket": str(mysql_socket)} if not WINDOWS else {
        "host": "127.0.0.1", "port": int(os.environ["DB_PORT"]), "password": os.environ["DB_PASSWORD"],
    }
    return pymysql.connect(**address, user="root", connect_timeout=1, read_timeout=5, write_timeout=5)


def wait_mysql(process: subprocess.Popen, mysql_socket: Path, stopped: threading.Event) -> None:
    """以真实连接判定数据库可用；退出、初始化失败和超时均阻止 API 启动。"""
    deadline = time.monotonic() + 60
    while process.poll() is None and not stopped.is_set():
        try:
            connection = mysql_connection(mysql_socket)
            connection.close()
            return
        except pymysql.MySQLError:
            if time.monotonic() >= deadline:
                break
            stopped.wait(0.2)
    raise RuntimeError("内置 MySQL 未能启动，请查看 backend/server.log。")


async def serve(listener: socket.socket, mysql: subprocess.Popen, stopped: threading.Event) -> None:
    """只在 lifespan 和关键读取接口通过后通知桌面；父进程退出或数据库停止时退出 API。"""
    import httpx

    # Uvicorn 的访问日志默认绑定 stdout，必须在配置 logger 前分离 IPC 和诊断输出。
    protocol = sys.stdout
    sys.stdout = sys.stderr
    server = uvicorn.Server(uvicorn.Config("server.app:app", log_level="info", timeout_graceful_shutdown=10))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        deadline = time.monotonic() + 60
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("内置 API 启动失败，请查看 backend/server.log。")
            if stopped.is_set() or time.monotonic() >= deadline:
                raise RuntimeError("内置 API 启动被中止或超时。")
            await asyncio.sleep(0.1)
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            for path in ("/template", "/api/templates/capabilities"):
                (await client.get(url + path)).raise_for_status()
        print(json.dumps({"url": url}), file=protocol, flush=True)
        while not task.done() and not stopped.is_set() and mysql.poll() is None:
            await asyncio.sleep(0.2)
    finally:
        server.should_exit = True
        await task


def main() -> None:
    """持有实例锁，初始化空数据库并监督 API；不删除已有数据，不连接宿主数据库。"""
    runtime, data = (Path(value).resolve() for value in sys.argv[1:3])
    os.umask(0o077)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.chmod(0o700)
    if WINDOWS:
        # chmod 不设置 Windows ACL；配置和数据库仅允许当前账户与 SYSTEM 读取。
        system = Path(os.environ["SystemRoot"]) / "System32"
        identity = subprocess.check_output([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"], creationflags=CREATION_FLAGS)
        sid = re.search(rb"S-1-5-[0-9-]+", identity).group().decode("ascii")
        subprocess.run([str(system / "icacls.exe"), str(data), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], stdout=sys.stderr, stderr=sys.stderr, check=True, creationflags=CREATION_FLAGS)
    with (data / "desktop.lock").open("a") as lock:
        try:
            lock_exclusive(lock)
        except OSError:
            raise RuntimeError("已有测试客户端正在使用此数据库，请使用已打开的窗口。") from None
        stopped = threading.Event()

        def parent_closed() -> None:
            """stdin 管道关闭覆盖正常退出与桌面进程崩溃，不依赖可复用的 PID。"""
            sys.stdin.buffer.read()
            stopped.set()

        threading.Thread(target=parent_closed, daemon=True).start()
        for number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(number, lambda *_: stopped.set())
        with tempfile.TemporaryDirectory(prefix="imv-db-") as temporary:
            mysql_socket = Path(temporary) / "mysql.sock"
            configure(runtime, data, mysql_socket)
            directory = data / "mysql"
            if not directory.exists():
                # 仅完整初始化的目录才能发布；中断重试不覆盖已存在的数据库。
                with tempfile.TemporaryDirectory(prefix="mysql-init-", dir=data) as initial:
                    process = subprocess.Popen(
                        [*mysql_command(runtime, Path(initial), mysql_socket), "--skip-networking", "--initialize-insecure"],
                        stdout=sys.stderr, stderr=sys.stderr, creationflags=CREATION_FLAGS,
                    )
                    try:
                        deadline = time.monotonic() + 120
                        while process.poll() is None and not stopped.wait(0.2):
                            if time.monotonic() >= deadline:
                                raise TimeoutError("MySQL 初始化超时。")
                        if stopped.is_set() or process.wait(timeout=1) != 0:
                            raise RuntimeError("MySQL 初始化失败或已取消。")
                        Path(initial).rename(directory)
                    finally:
                        stop(process)
            if stopped.is_set():
                return
            command = mysql_command(runtime, directory, mysql_socket)
            if WINDOWS:
                # init-file 在接受客户端连接前设置密码；每次生成，启动成功即删除，不放入命令行。
                initialization = data / "mysql-init.sql"
                initialization.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{os.environ['DB_PASSWORD']}';\n", encoding="utf-8")
                command.append(f"--init-file={initialization}")
            mysql = subprocess.Popen(command, stdout=sys.stderr, stderr=sys.stderr, creationflags=CREATION_FLAGS)
            try:
                wait_mysql(mysql, mysql_socket, stopped)
                if WINDOWS:
                    initialization.unlink()
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(128)
                    listener.setblocking(False)
                    asyncio.run(serve(listener, mysql, stopped))
            finally:
                if WINDOWS and mysql.poll() is None:
                    # Windows terminate() 不发送 SIGTERM，先请求 MySQL 正常刷盘退出。
                    try:
                        with mysql_connection(mysql_socket) as connection:
                            connection.cursor().execute("SHUTDOWN")
                    except pymysql.MySQLError:
                        pass  # SHUTDOWN 可在回执前断开连接，仍须等待数据库刷盘。
                    try:
                        mysql.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pass
                stop(mysql)
                if WINDOWS:
                    initialization.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
