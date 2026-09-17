"""验证打包脚本的稳定版本选择、原生依赖闭包与最终安装包的启动路径。"""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("build_ffmpeg", Path(__file__).resolve().parents[2] / ".github/scripts/build-ffmpeg.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)




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
