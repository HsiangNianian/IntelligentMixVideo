"""验证桌面启动配置与进程清理；IMV_TEST_BUNDLE 指向解包运行时后验证真实 MySQL/API/渲染。

常规：uv run --locked --project server pytest server/tests/test_desktop.py。
真实用例只使用临时用户目录，不连接外部 MySQL 或模型服务。
"""

import json
import asyncio
from io import StringIO
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server import database, desktop
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings

BUNDLE = os.environ.get("IMV_TEST_BUNDLE")
APPDIR = os.environ.get("IMV_TEST_APPDIR")
DESKTOP = os.environ.get("IMV_TEST_DESKTOP_EXECUTABLE")
PYTHON = "python/python.exe" if sys.platform == "win32" else "python/bin/python3"


def test_desktop_config_preserves_credentials_and_uses_private_paths(tmp_path, monkeypatch):
    """用户配置保留，工具和数据库只能指向随包资源与私有数据目录。"""
    runtime, data = tmp_path / "runtime", tmp_path / "data"
    runtime.mkdir()
    data.mkdir()
    (runtime / ".env.example").write_text("IMV_ACTOR_API_KEY=\n")
    (data / ".env").write_text("IMV_ACTOR_API_KEY=user-config\nDB_HOST=remote.test\n")
    # configure 会变更进程环境；本例由 monkeypatch 在结束后恢复全部环境。
    for key in ["PATH", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_SOCKET",
                "IMV_ACTOR_API_KEY", "IMV_DATA_DIR", "IMV_RENDERER_DIR", "IMV_BROWSER_EXECUTABLE",
                "IMV_FONT_REGULAR", "IMV_FONT_BOLD", "IMV_RUNTIME_LIB_DIR"]:
        monkeypatch.setenv(key, os.environ.get(key, ""))
        if key == "IMV_ACTOR_API_KEY":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    assert desktop.configure(runtime, data, tmp_path / "mysql.sock") == 0
    assert os.environ["IMV_ACTOR_API_KEY"] == "user-config"
    assert os.environ["DB_HOST"] == "localhost"
    assert os.environ["IMV_DATA_DIR"] == str(data / "remotion")
    assert (data / ".env").read_text().startswith("IMV_ACTOR_API_KEY=user-config")
    # 上游统一配置基类后，实际设置对象仍以桌面加载的用户配置和私有路径为准。
    settings = Settings()
    assert settings.actor_api_key.get_secret_value() == "user-config"
    assert settings.data_dir == data / "remotion"
    assert settings.runtime_lib_dir == runtime / "lib"
    assert database.DatabaseSettings().socket == str(tmp_path / "mysql.sock")


def test_debug_local_settings_are_loaded_before_business(tmp_path, monkeypatch):
    """本地配置覆盖内置默认值供实际配置类读取；路径/端口生效，数据库和原文件不变。"""
    from server.asr.settings import ASRSettings
    from server.segmentation.settings import Settings as SegmentationSettings
    from server.video_composition.settings import Settings as CompositionSettings

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.chdir(tmp_path)
    runtime, data = tmp_path / "runtime", tmp_path / "backend"
    runtime.mkdir()
    data.mkdir()
    (data / ".env").write_text("IMV_ACTOR_MODEL=server-default\n")
    saved = {
        "startup": {"port": 23456, "DB_HOST": "ignored.test"},
        "remotion_agent": {"actor_model": "client-model", "max_tokens": 45000, "job_timeout_seconds": 90,
                           "data_dir": "custom-data", "runtime_lib_dir": "", "enforce_model_budget": False},
        "asr": {"dashscope_api_key": "client-asr"},
        "segmentation": {"llm_base_url": "https://model.test", "llm_api_key": "client-key", "llm_model": "client-model", "allow_insecure_llm_http": False},
        "ims": {"ims_access_key_id": "client-id", "ims_access_key_secret": "client-secret",
                "match_base_url": "https://match.test", "match_authorization": "client-match", "composition_width": 640},
        "database": {"host": "ignored.test"},
    }
    path = tmp_path / "data/settings/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(saved))
    assert desktop.configure(runtime, data, tmp_path / "mysql.sock") == 23456
    models = Settings()
    assert (models.actor_model, models.max_tokens, models.job_timeout_seconds) == ("client-model", 45000, 90)
    assert models.data_dir == data / "custom-data" and models.runtime_lib_dir == runtime / "lib"
    assert not models.enforce_model_budget and not SegmentationSettings().allow_insecure_llm_http
    assert ASRSettings().dashscope_api_key.get_secret_value() == "client-asr"
    composition = CompositionSettings()
    assert composition.composition_width == 640 and composition.match_base_url == "https://match.test"
    assert composition.ims_access_key_secret.get_secret_value() == "client-secret"
    assert composition.match_authorization.get_secret_value() == "client-match"
    assert database.DatabaseSettings().host == "localhost"
    assert (data / ".env").read_text() == "IMV_ACTOR_MODEL=server-default\n"
    assert json.loads(path.read_text()) == saved
    saved["startup"]["port"] = 0
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        desktop.configure(runtime, data, tmp_path / "mysql.sock")


@pytest.mark.parametrize("key", ["IMV_VISION_API_KEY", "DASHSCOPE_API_KEY", "COMPOSITION_PUBLIC_BASE_URL", "SEGMENT_MATCH_AUTHORIZATION", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "PORT", "imv_llm_api_key"])
def test_desktop_discards_host_configuration(tmp_path, monkeypatch, key):
    """宿主模型凭据不覆盖用户文件，文件中未配置的宿主配置也不能残留。"""
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv(key, "host-secret")
    monkeypatch.setenv("IMV_ACTOR_API_KEY", "host-actor-secret")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    runtime, data = tmp_path / "runtime", tmp_path / "data"
    runtime.mkdir()
    data.mkdir()
    (data / ".env").write_text("IMV_ACTOR_API_KEY=user-config\n")
    monkeypatch.chdir(tmp_path)
    desktop.configure(runtime, data, tmp_path / "mysql.sock")
    assert key not in os.environ
    assert "PYTHON_DOTENV_DISABLED" not in os.environ
    assert Settings().actor_api_key.get_secret_value() == "user-config"
    assert (data / ".env").read_text() == "IMV_ACTOR_API_KEY=user-config\n"


def test_windows_tool_directory_comes_from_system_api(monkeypatch):
    """即使宿主伪造 SystemRoot，ACL 工具目录仍由 Windows API 决定；失败则停止。"""
    import ctypes

    def directory(buffer, size):
        """模拟系统 API 写入缓冲区，不启动 Windows 子进程。"""
        buffer.value = "C:/Windows/System32"
        return len(buffer.value)

    monkeypatch.setenv("SystemRoot", "C:/untrusted")
    api = SimpleNamespace(GetSystemDirectoryW=directory)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=api), raising=False)
    assert desktop.windows_system_directory() == Path("C:/Windows/System32")
    api.GetSystemDirectoryW = lambda *args: 0
    with pytest.raises(OSError):
        desktop.windows_system_directory()


def test_socket_survives_database_bootstrap(monkeypatch, tmp_path):
    """带 socket 的连接 URL 在去掉库名后仍走同一私有 MySQL，不回退宿主 3306。"""
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setenv("DB_SOCKET", str(tmp_path / "mysql.sock"))
    settings_type = database.DatabaseSettings
    monkeypatch.setattr(database, "DatabaseSettings", lambda: settings_type(_env_file=None))
    engine = database.get_engine()
    try:
        assert engine.url._replace(database=None).query["unix_socket"] == str(tmp_path / "mysql.sock")
    finally:
        database.close_database()


@pytest.mark.parametrize("configured", [True, False])
def test_ssl_ca_is_forwarded_to_driver(configured, monkeypatch, tmp_path):
    """配置 DB_SSL_CA 时 CA 路径进入驱动连接参数；未配置时不携带该键。"""
    ca = tmp_path / "mysql-ca.pem"
    ca.write_text("test-ca", encoding="utf-8")
    if configured:
        monkeypatch.setenv("DB_SSL_CA", str(ca))
    monkeypatch.setattr(database, "_engine", None)
    settings_type = database.DatabaseSettings
    monkeypatch.setattr(database, "DatabaseSettings", lambda: settings_type(_env_file=None))
    captured = {}
    real_create_engine = database.create_engine

    def capture(url, **kwargs):
        """记录宿主传给 SQLAlchemy 的连接参数，再交给真实实现。"""
        captured.update(kwargs)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(database, "create_engine", capture)
    try:
        database.get_engine()
    finally:
        database.close_database()
    if configured:
        assert captured["connect_args"]["ssl_ca"] == str(ca)
    else:
        assert "ssl_ca" not in captured["connect_args"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux bubblewrap mount contract")
def test_renderer_only_mounts_private_libraries(tmp_path):
    """随包库进入只读 sandbox，仍清空宿主环境且关闭网络。"""
    command = Renderer(Settings(_env_file=None, runtime_lib_dir=tmp_path / "lib")).command(tmp_path)
    assert command[command.index("--clearenv") + 1:][:3] == ["--setenv", "LD_LIBRARY_PATH", "/runtime-lib"]
    assert "--unshare-all" in command
    assert "/runtime-prlimit" in command
    assert str(tmp_path / "lib") in command


def test_failed_mysql_and_child_cleanup():
    """数据库提前退出立即报错；清理函数回收仍运行的子进程。"""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    desktop.stop(process)
    assert process.poll() is not None
    with pytest.raises(RuntimeError, match="MySQL"):
        desktop.wait_mysql(process, Path("/nonexistent/mysql.sock"), threading.Event())


def test_readiness_protocol_excludes_http_logs(monkeypatch):
    """真实 Uvicorn 访问日志只能进入诊断流，桌面读取的首行必须是就绪 JSON。"""
    application = FastAPI()

    @application.get("/{path:path}")
    def ready(path: str):
        """仅替换数据库业务边界，保留真实 HTTP、Uvicorn 和就绪握手。"""
        return {"path": path}

    config_type = desktop.uvicorn.Config
    monkeypatch.setattr(desktop.uvicorn, "Config", lambda *args, **kwargs: config_type(application, use_colors=False))
    protocol, diagnostics = StringIO(), StringIO()
    monkeypatch.setattr(sys, "stdout", protocol)
    monkeypatch.setattr(sys, "stderr", diagnostics)

    async def scenario():
        """等回执后模拟父进程关闭，所有监听与任务均有界清理。"""
        stopped = threading.Event()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            listener.setblocking(False)
            task = asyncio.create_task(desktop.serve(listener, SimpleNamespace(poll=lambda: None), stopped))
            try:
                async with asyncio.timeout(10):
                    while not protocol.getvalue():
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
            finally:
                stopped.set()
                await asyncio.wait_for(task, timeout=10)

    asyncio.run(scenario())
    assert json.loads(protocol.getvalue())["url"].startswith("http://127.0.0.1:")
    assert "GET /template HTTP/1.1" in diagnostics.getvalue()


@pytest.fixture
def bundle(tmp_path):
    """明确开启时才用随包 Python；HTTP、数据库、配置和日志均限定临时目录。"""
    if not BUNDLE:
        pytest.skip("Set IMV_TEST_BUNDLE to the extracted backend runtime")
    runtime = Path(BUNDLE).resolve()
    env = {"PATH": str(runtime / "bin") + os.pathsep + os.defpath, "HOME": str(tmp_path), "LANG": "C.UTF-8", "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        env.update({key: os.environ[key] for key in ("SystemRoot", "ComSpec") if key in os.environ})
        env.update(TEMP=str(tmp_path), TMP=str(tmp_path), USERPROFILE=str(tmp_path))
    return runtime, tmp_path / "data", env


def launch_bundle(runtime, data, env, log):
    """等真实就绪回执，超时回收进程并保留诊断，不借用开发环境 Python。"""
    process = subprocess.Popen(
        [str(runtime / PYTHON), "-I", "-X", "utf8", "-m", "server.desktop", str(runtime), str(data)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env,
    )
    try:
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        response = json.loads(lines.get(timeout=240))
        return process, response["url"]
    except Exception as error:
        process.stdin.close()
        process.wait(timeout=45)
        log.flush()
        raise AssertionError(f"{error}\n{Path(log.name).read_text()}") from error


def close_bundle(process):
    """模拟桌面正常退出或崩溃造成的管道关闭，必须完成数据库落盘后退出。"""
    process.stdin.close()
    assert process.wait(timeout=45) == 0


def test_bundle_start_restart_and_cleanup(bundle, tmp_path, template_payload):
    """解包运行时首次可读写模板，无模型密钥可启动；重启保留数据，重复实例拒绝抢库。"""
    runtime, data, env = bundle
    with (tmp_path / "server.log").open("wb") as log:
        process, url = launch_bundle(runtime, data, env, log)
        try:
            with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
                assert client.get("/api/templates/capabilities").json()["models_configured"] is False
                response = client.post("/template", json=template_payload)
                assert response.status_code == 201, response.text
                identifier = response.json()["template_id"]
                duplicate = subprocess.run(
                    [str(runtime / PYTHON), "-I", "-X", "utf8", "-m", "server.desktop", str(runtime), str(data)],
                    env=env, input=b"", capture_output=True, timeout=20,
                )
                assert duplicate.returncode != 0
                assert "已有测试客户端" in duplicate.stderr.decode()
        finally:
            close_bundle(process)
        process, restarted = launch_bundle(runtime, data, env, log)
        try:
            with httpx.Client(base_url=restarted, timeout=10, trust_env=False) as client:
                assert client.get(f"/template/{identifier}").status_code == 200
                assert client.delete(f"/template/{identifier}").status_code == 204
        finally:
            close_bundle(process)
        # 第二次启动成功也验证 MySQL 已停止、数据锁释放，API 端口在退出后不可连接。
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            httpx.get(restarted, timeout=2, trust_env=False)


@pytest.mark.skipif(sys.platform != "linux", reason="Native render sandbox adaptation is validated separately")
def test_bundle_real_renderer(bundle, tmp_path):
    """使用随包 Node/Chrome/FFprobe/font 和共享库真实渲染，不调用模型或宿主工具链。"""
    runtime, data, env = bundle
    data.mkdir()
    script = '''
import asyncio, sys
from pathlib import Path
from server.desktop import configure
from server.remotion_templates.settings import load_settings
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.models import TemplateCandidate, TemplateSpec, CompositionConfig, TextLayer
from server.remotion_templates.harness import controls
runtime, data = map(Path, sys.argv[1:3])
configure(runtime, data, data / "unused.sock")
spec = TemplateSpec(name="标题", description="白色文字", composition=CompositionConfig(width=320, height=240, duration_in_frames=6), text_layers=[TextLayer(id="title", text="你好", end_frame=6)])
schema, defaults = controls(spec)
code = """/** Offline bundle smoke composition. */
import React from "react";
import {AbsoluteFill} from "remotion";
/** Render props directly for deterministic parameter probes. */
export default function Template(p: Record<string, string | number>) {
return <AbsoluteFill><div style={{position: "absolute", left: Number(p["0_layout_x"])*100+"%", top: Number(p["0_layout_y"])*100+"%", width: Number(p["0_layout_width"])*100+"%", transform: `translate(-50%, -50%) rotate(${p["0_layout_rotation"]}deg)`, textAlign: String(p["0_layout_align"]) as React.CSSProperties["textAlign"], fontFamily: String(p["0_style_font_family"]), fontSize: Number(p["0_style_font_size"]), fontWeight: Number(p["0_style_font_weight"]), lineHeight: Number(p["0_style_line_height"]), letterSpacing: Number(p["0_style_letter_spacing"]), color: String(p["0_style_color"]), whiteSpace: "pre-wrap"}}>{p["0_text"]}</div></AbsoluteFill>;
}
"""
candidate = TemplateCandidate(tsx_code=code, config_schema=schema, default_config=defaults)
_, report = asyncio.run(Renderer(load_settings()).validate(candidate, spec, data / "render"))
assert all(check.status == "pass" for check in report.checks), report.model_dump()
assert (data / "render/preview.mp4").stat().st_size > 1000
'''
    result = subprocess.run(
        [str(runtime / PYTHON), "-I", "-X", "utf8", "-c", script, str(runtime), str(data)],
        env=env, capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr + (
        (data / "render/worker.log").read_text() if (data / "render/worker.log").exists() else ""
    )


def test_bundle_desktop_starts_api_before_home(tmp_path):
    """在 Xvfb 中启动 AppRun；后端自检后首页再次读取模板列表才算接入成功。"""
    if not APPDIR:
        pytest.skip("Set IMV_TEST_APPDIR to the extracted AppImage")
    data = tmp_path / "data"
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8",
        "APPDIR": APPDIR, "XDG_DATA_HOME": str(data),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    server_log = data / "com.intelligentmixvideo.client/backend/server.log"
    output = tmp_path / "desktop.log"
    with output.open("wb") as log:
        process = subprocess.Popen(
            ["xvfb-run", "-a", str(Path(APPDIR) / "AppRun")],
            env=env, stdout=log, stderr=log, start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 180
            while process.poll() is None and time.monotonic() < deadline:
                if server_log.exists():
                    content = server_log.read_text()
                    if content.count('GET /template HTTP/1.1" 200') >= 2:
                        return
                time.sleep(0.5)
            log.flush()
            pytest.fail(output.read_text() + (server_log.read_text() if server_log.exists() else "\nNo bundled API log"))
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=30)


def test_exclusive_file_lock_releases_after_close(tmp_path):
    """真实跨进程验证争锁失败；持有者关闭句柄后另一进程可以取得同一把锁。"""
    from server.file_lock import lock_exclusive

    script = "from server.file_lock import lock_exclusive; import sys; f=open(sys.argv[1], 'a'); lock_exclusive(f)"
    path = tmp_path / "state.lock"
    with path.open("a") as lock:
        lock_exclusive(lock)
        conflict = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=10)
        assert conflict.returncode != 0
    released = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=10)
    assert released.returncode == 0, released.stderr


def test_windows_mysql_uses_password_and_loopback(tmp_path, monkeypatch):
    """Windows 仅绑定回环且不使用默认端口/空密码；初始化密码不出现在 mysqld 参数中。"""
    monkeypatch.setattr(desktop, "WINDOWS", True)
    monkeypatch.setenv("DB_PORT", "23456")
    monkeypatch.setenv("DB_PASSWORD", "private-secret")
    command = desktop.mysql_command(tmp_path, tmp_path / "data", tmp_path / "mysql.sock")
    assert command[0].endswith("mysqld.exe")
    assert "--bind-address=127.0.0.1" in command
    assert "--port=23456" in command
    assert not any("private-secret" in arg for arg in command)


def test_bundle_tools_run_without_development_path(bundle):
    """实际运行归档里的各工具，验证平台架构、动态库与 Python 原生模块可加载。"""
    runtime, data, env = bundle
    suffix = ".exe" if sys.platform == "win32" else ""
    for name, flag in [("node", "--version"), ("bun", "--version"), ("uv", "--version"),
                       ("ffmpeg", "-version"), ("ffprobe", "-version"), ("mysqld", "--version")]:
        result = subprocess.run([str(runtime / "bin" / (name + suffix)), flag], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, (name, result.stderr)
    provenance = json.loads((runtime / "licenses/ffmpeg/source.json").read_text())
    for name in ("ffmpeg", "ffprobe"):
        version = subprocess.check_output([str(runtime / "bin" / (name + suffix)), "-version"], env=env, text=True)
        assert version.startswith(name + " version " + provenance["tag"].removeprefix("n") + " ")
    result = subprocess.run([str(runtime / PYTHON), "-I", "-X", "utf8", "-c", "import server.app, PIL._imaging, cryptography.hazmat.bindings._rust"], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    video = data.parent / "tool-test.mp4"
    subprocess.run([str(runtime / "bin" / ("ffmpeg" + suffix)), "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64", "-frames:v", "1", "-c:v", "mpeg4", str(video)], env=env, check=True, timeout=30)
    metadata = subprocess.check_output([str(runtime / "bin" / ("ffprobe" + suffix)), "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(video)], env=env, timeout=30)
    assert json.loads(metadata)["streams"] == [{"width": 64, "height": 64}]


@pytest.mark.skipif(sys.platform == "linux", reason="Linux Chromium is tested by the real sandbox rendering pipeline")
def test_bundle_native_browser(bundle):
    """Windows/macOS 尚无生成沙箱，先独立验证随包 Remotion 与 Chrome；Linux 使用完整渲染验收。"""
    runtime, _, env = bundle
    suffix = ".exe" if sys.platform == "win32" else ""
    manifest = runtime / "runtime.json"
    browser = runtime / (json.loads(manifest.read_text())["browser"] if manifest.exists() else "chrome/chrome")
    # 使用 worker.mjs 同一浏览器入口和图形后端；只执行固定 DOM 探针，不运行生成代码。
    script = '''
import {openBrowser} from "@remotion/renderer";
const browser = await openBrowser("chrome", {
  browserExecutable: process.argv[1], logLevel: "error", chromiumOptions: {gl: "swangle"},
});
try {
  const page = await browser.newPage({
    context: () => null, logLevel: "error", indent: false, pageIndex: 0,
    onBrowserLog: null, onLog: ({previewString}) => console.error(previewString),
  });
  if (await page.evaluate(() => document.documentElement.tagName) !== "HTML") {
    throw new Error("Bundled Chromium did not load a document");
  }
} finally {
  await browser.close({silent: true});
}
'''
    page = subprocess.run([str(runtime / "bin" / ("node" + suffix)), "--input-type=module", "-e", script, str(browser)], cwd=runtime / "renderer", env=env, capture_output=True, timeout=45)
    if page.returncode:
        pytest.fail(page.stderr.decode("utf-8", errors="replace"))


def test_bundle_native_desktop_starts_api_before_home(tmp_path):
    """在干净的原生 CI 账户启动客户端；后端自检后首页再次读取模板列表才算成功。"""
    if not DESKTOP:
        pytest.skip("Set IMV_TEST_DESKTOP_EXECUTABLE on an isolated native CI runner")
    parent = Path(os.environ["APPDATA"]) if sys.platform == "win32" else Path.home() / "Library/Application Support"
    app_data = parent / "com.intelligentmixvideo.client"
    assert not app_data.exists(), "Desktop smoke requires a clean CI account; existing user data must be preserved"
    server_log = app_data / "backend/server.log"
    output = tmp_path / "desktop.log"
    with output.open("wb") as log:
        process = subprocess.Popen([DESKTOP], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 240
            while process.poll() is None and time.monotonic() < deadline:
                if server_log.exists() and server_log.read_text(encoding="utf-8", errors="replace").count('GET /template HTTP/1.1" 200') >= 2:
                    return
                time.sleep(0.5)
            log.flush()
            diagnostics = Path(os.environ.get("RUNNER_TEMP", str(tmp_path))) / "imv-desktop-diagnostics"
            diagnostics.mkdir(exist_ok=True)
            shutil.copy2(output, diagnostics / "desktop.log")
            if server_log.exists():
                shutil.copy2(server_log, diagnostics / "server.log")
            if sys.platform == "darwin":
                subprocess.run(["/usr/sbin/screencapture", "-x", str(diagnostics / "desktop.png")], timeout=15, check=False)
            pytest.fail(output.read_text(errors="replace") + (server_log.read_text(errors="replace") if server_log.exists() else "\nNo bundled API log"))
        finally:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid)], capture_output=True, timeout=15)
            else:
                process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, timeout=15)
                else:
                    process.kill()
                process.wait(timeout=10)
            # 父进程退出后 Python 异步关闭数据库；这里不删除正在使用的数据。
