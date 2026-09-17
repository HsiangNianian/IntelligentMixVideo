"""验证打包脚本的稳定版本选择、原生依赖闭包与最终安装包的启动路径。"""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("build_ffmpeg", Path(__file__).resolve().parents[2] / ".github/scripts/build-ffmpeg.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


@pytest.mark.parametrize("script", ["bundle-backend.py", "bundle_native.py"])
def test_build_arguments_are_not_shell_commands(tmp_path, script):
    """真实子进程原样接收空格和 shell 元字符，不能执行参数中的第二条命令。"""
    spec = importlib.util.spec_from_file_location("bundle_arguments", SPEC.origin.replace("build-ffmpeg.py", script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = tmp_path / "injected"
    payload = f"space ; touch {marker} $(touch {marker})"
    assert module.run(sys.executable, "-c", "import sys; print(sys.argv[1])", payload) == payload
    assert not marker.exists()


@pytest.mark.parametrize("dynamic,status,stdout,stderr", [
    (True, 1, "", "unsupported loader"),
    (True, 0, "", "libtest.so => not found"),
    (False, 0, "", ""),
    (True, 0, "", ""),
])
def test_linux_dependency_inspection_rejects_failures(tmp_path, monkeypatch, dynamic, status, stdout, stderr):
    """动态检查失败必须报错，静态工具跳过 ldd；完整依赖输出中的间接库也会复制。"""
    spec = importlib.util.spec_from_file_location("bundle_elf", SPEC.origin.replace("build-ffmpeg.py", "bundle-backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "tool").write_bytes(b"\x7fELF")
    libraries = [tmp_path / "libdirect.so", tmp_path / "libindirect.so"]
    for library in libraries:
        library.write_bytes(library.name.encode())
    calls = []

    def run(command, **kwargs):
        """模拟平台检查输出，文件扫描与依赖复制仍使用真实实现。"""
        calls.append(command)
        if command[0] == "readelf":
            return subprocess.CompletedProcess(command, 0, "(NEEDED)" if dynamic else "")
        if command[0] == "ldd":
            output = stdout or "\n".join(f"{library.name} => {library} (0x00)" for library in libraries)
            return subprocess.CompletedProcess(command, status, output, stderr)
        return subprocess.CompletedProcess(command, 1 if command[0] == "patchelf" else 0, "", "")

    # check_output 经 Popen 调用；在模块封装处只替换需要读取文本的构建工具。
    monkeypatch.setattr(module, "run", lambda *args: run(list(args)).stdout)
    monkeypatch.setattr(module.subprocess, "run", run)
    if status or "not found" in stdout + stderr:
        with pytest.raises(RuntimeError, match=stderr):
            module.shared_libraries(runtime)
    else:
        module.shared_libraries(runtime)
        assert any(call[0] == "ldd" for call in calls) == dynamic
        for library in libraries:
            assert (runtime / "lib" / library.name).exists() == dynamic


@pytest.mark.skipif(sys.platform == "win32", reason="Windows test accounts may lack symlink privileges")
@pytest.mark.parametrize("native_test_fails", [False, True])
def test_macos_smoke_resolves_temporary_symlinks(tmp_path, monkeypatch, native_test_fails):
    """模拟 /var 到 /private/var：传给桌面的路径不含软链接，验收失败也必须卸载 DMG。"""
    from contextlib import nullcontext

    spec = importlib.util.spec_from_file_location("native_smoke", Path(__file__).resolve().parents[2] / ".github/scripts/native-bundle-smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    work = tmp_path / "private var"
    work.mkdir()
    alias = tmp_path / "var"
    alias.symlink_to(work, target_is_directory=True)
    installers = tmp_path / "bundle/dmg"
    installers.mkdir(parents=True)
    (installers / "client.dmg").touch()
    monkeypatch.setattr(smoke.sys, "platform", "darwin")
    monkeypatch.setattr(smoke.sys, "argv", ["native-bundle-smoke.py", str(installers.parent)])
    monkeypatch.setattr(smoke.tempfile, "TemporaryDirectory", lambda **_: nullcontext(str(alias)))
    calls = []

    def run(command, **kwargs):
        """只模拟 macOS 外部工具；实际文件和软链接用于复现 Tauri 的路径限制。"""
        calls.append(command)
        if command[:2] == ["hdiutil", "attach"]:
            contents = Path(command[-1]) / "Client.app/Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources").mkdir()
            (contents / "MacOS/client").touch()
            (contents / "Resources/backend.tar").touch()
        elif command[0] == "uv":
            executable = Path(kwargs["env"]["IMV_TEST_DESKTOP_EXECUTABLE"])
            assert executable.is_file()
            assert not any(path.is_symlink() for path in (executable, *executable.parents))
            assert (executable.parent.parent / "Resources/backend.tar").is_file()
            if native_test_fails:
                raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    if native_test_fails:
        with pytest.raises(subprocess.CalledProcessError):
            smoke.main()
    else:
        smoke.main()
    assert any(command[0] == "uv" for command in calls)
    assert calls[-1] == ["hdiutil", "detach", str(work.resolve() / "installer")]


def test_latest_stable_ffmpeg_preserves_commit():
    """排除更新的开发/预发布 tag，数字版本比较不把 9.9 排在 9.10 之后。"""
    tags = [{"name": name, "commit": {"sha": name + "-commit"}} for name in ["n9.9.9", "v0.6", "n10.0-dev", "n10.0-rc1", "n9.10.1", "n9.10"]]
    assert BUILD.latest_stable_tag(tags) == tags[4]


@pytest.mark.parametrize("tags", [[], [{"name": "master"}], [{"name": "n10.0-dev"}]])
def test_no_stable_ffmpeg_fails_closed(tags):
    """没有正式 tag 时停止构建，不静默回退到宿主旧版或 nightly。"""
    with pytest.raises(ValueError):
        BUILD.latest_stable_tag(tags)


def test_macos_library_closure_keeps_same_named_versions_separate(tmp_path, monkeypatch):
    """模拟 Universal2 的真实 otool 输出：忽略架构标题/库自身 ID，保留两份不同同名依赖。"""
    spec = importlib.util.spec_from_file_location("bundle_native", Path(__file__).resolve().parents[2] / ".github/scripts/bundle_native.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    sources, origins, changes = {}, {}, []
    for name in ("python", "mysql", "compositor"):
        source = tmp_path / name
        source.mkdir()
        library = source / "libsame.dylib"
        library.write_bytes(b"\xcf\xfa\xed\xfe" + name.encode())
        binary = runtime / "bin" / name
        binary.write_bytes(b"\xca\xfe\xba\xbe")
        original = source / name
        original.write_bytes(binary.read_bytes())
        origins[binary] = original
        sources[name] = library

    def tool(*args):
        """只替换 Mac 专属工具；复制文件、依赖闭包和相对路径修改使用真实实现。"""
        if args[0] == "otool":
            path = Path(args[-1])
            if args[1] == "-l":
                if path.name == "python":
                    return "cmd LC_RPATH\ncmdsize 48\npath @loader_path (offset 12)"
                return "cmd LC_ID_DYLIB\ncmdsize 48\nname @rpath/libsame.dylib (offset 24)" if path.name == "libsame.dylib" else ""
            if path.name in sources:
                dependency = {"python": "@rpath/libsame.dylib", "compositor": "libsame.dylib"}.get(path.name, sources[path.name])
                return f"{path} (architecture x86_64):\n\t{dependency} (compatibility version 1.0.0)\n{path} (architecture arm64):\n\t{dependency} (compatibility version 1.0.0)"
            return f"{path}:\n\t@rpath/libsame.dylib (compatibility version 1.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)"
        if args[0] == "install_name_tool":
            changes.append(args)
        return ""

    monkeypatch.setattr(native, "run", tool)
    native.macos_libraries(runtime, origins)
    libraries = list((runtime / "lib").rglob("libsame.dylib"))
    assert {path.read_bytes() for path in libraries} == {path.read_bytes() for path in sources.values()}
    for _, _, original, replacement, binary in changes:
        target = Path(binary).parent / replacement.removeprefix("@loader_path/")
        assert target.read_bytes() == sources[Path(binary).name].read_bytes()
        assert target.resolve().is_relative_to(runtime)


def test_release_resolution_exports_exact_cache_identity(tmp_path, monkeypatch):
    """构建前解析的同一提交用于缓存和源码下载，包含空格的运行目录原样传给后续步骤。"""
    import sys

    output, environment = tmp_path / "output", tmp_path / "environment"
    root = tmp_path / "runner with spaces"
    monkeypatch.setenv("RUNNER_TEMP", str(root))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_ENV", str(environment))
    monkeypatch.setattr(sys, "argv", ["build-ffmpeg.py", "--resolve"])
    monkeypatch.setattr(BUILD, "resolve_release", lambda: {"name": "n9.0.1", "commit": {"sha": "a" * 40}})
    BUILD.main()
    assert output.read_text() == "commit=" + "a" * 40 + "\n"
    assert "IMV_FFMPEG_COMMIT=" + "a" * 40 + "\n" in environment.read_text()
    assert f"IMV_FFMPEG_DIR={root / 'imv-ffmpeg/install'}\n" in environment.read_text()
    assert not root.exists()  # 解析阶段不触发下载或编译。


@pytest.mark.parametrize("installed_wheel", [False, True])
def test_macos_preserves_working_relative_rpath(tmp_path, monkeypatch, installed_wheel):
    """内部库和后装 wheel 均从实际文件解析，不增长相对 load command 或误找源 Python。"""
    spec = importlib.util.spec_from_file_location("bundle_native", Path(__file__).resolve().parents[2] / ".github/scripts/bundle_native.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    runtime = tmp_path / "runtime"
    extension = runtime / "python/lib/python3.12/lib-dynload/_tkinter.so"
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"\xcf\xfa\xed\xfe")
    library = runtime / "python/lib/libtcl9.0.dylib"
    library.write_bytes(b"\xcf\xfa\xed\xfe")
    helper = runtime / "chrome/Chrome.app/Helper (Renderer)"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"\xcf\xfa\xed\xfe")

    def tool(*args):
        """模拟真实 Python 相对 RPATH；任何不必要的修改都令回归失败。"""
        assert args[0] == "otool", args
        assert Path(args[-1]).exists(), args
        assert Path(args[-1]) != helper  # 完整 Chrome 发行目录不拆开重写或破坏签名。
        if args[1] == "-l":
            return "cmd LC_RPATH\ncmdsize 48\npath @loader_path/../.. (offset 12)"
        if Path(args[-1]) == extension:
            return f"{extension}:\n\t@rpath/libtcl9.0.dylib (compatibility version 1.0.0)"
        return f"{library}:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)"

    monkeypatch.setattr(native, "run", tool)
    origins = {runtime / "python": tmp_path / "source-python"} if installed_wheel else {}
    native.macos_libraries(runtime, origins)
    assert not list((runtime / "lib").iterdir())
