"""在各平台 CI 收集原生内置后端，保留锁定依赖、共享库和许可证，生成 Tauri 资源归档。

运行：python .github/scripts/bundle-backend.py <Chrome executable>。
不复制开发机 .env、数据库、缓存或密钥；仅在 IMV_DEBUG=true 打包时调用。
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args: str | Path) -> str:
    """执行构建工具并拒绝失败，输出仅用于解析路径与依赖。"""
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def elf(path: Path) -> bool:
    """识别二进制及原生模块，避免对普通资源调用 ldd。"""
    with path.open("rb") as source:
        return source.read(4) == b"\x7fELF"


def shared_libraries(runtime: Path) -> None:
    """收集 ELF 的依赖闭包，排除宿主 glibc；私有 RPATH 不影响桌面 WebKit/PulseAudio。"""
    libraries = runtime / "lib"
    libraries.mkdir()
    notices = runtime / "licenses"
    notices.mkdir()
    paths = [path for path in runtime.rglob("*") if path.is_file() and not path.is_symlink() and elf(path)]
    packages = {"mysql-server-core-8.0", "bubblewrap", "util-linux", "fonts-noto-cjk"}
    missing = []
    for path in paths:
        result = subprocess.run(
            ["ldd", str(path)], text=True, capture_output=True,
            env={**os.environ, "LD_LIBRARY_PATH": f"{path.parent}:{runtime / 'python/lib'}"},
        )
        if "not found" in result.stdout:
            missing.append(f"{path}: {result.stdout}")
            continue
        for name in re.findall(r"(?:=>\s+|^\s*)(/[^\s]+)", result.stdout, re.M):
            dependency = Path(name)
            # glibc 必须与宿主动态加载器一致，最低系统基线为 Ubuntu 22.04/glibc 2.35。
            if re.match(r"(?:ld-linux|lib(?:c|m|mvec|pthread|dl|rt|resolv|util|anl|nss_[^.]*)\.so)", dependency.name):
                continue
            if dependency.is_relative_to(runtime):
                continue
            target = libraries / dependency.name
            if not target.exists():
                shutil.copy2(dependency, target)
                owner = subprocess.run(["dpkg-query", "-S", str(dependency)], text=True, capture_output=True)
                if owner.returncode == 0:
                    packages.add(owner.stdout.split(": ")[0].split(":")[0])
    if missing:
        raise RuntimeError("Unresolved runtime dependencies:\n" + "\n".join(missing))
    # DT_RPATH 同时覆盖传递依赖；各工具使用自己的相对目录，不继承 AppImage 的 LD_LIBRARY_PATH。
    for path in [*paths, *libraries.iterdir()]:
        previous = subprocess.run(["patchelf", "--print-rpath", str(path)], text=True, capture_output=True)
        if previous.returncode:
            continue  # Bun/uv 等静态程序没有动态段。
        relative = os.path.relpath(libraries, path.parent)
        rpath = "$ORIGIN:$ORIGIN/" + relative + ":$ORIGIN/" + os.path.relpath(runtime / "python/lib", path.parent)
        if previous.stdout.strip():
            rpath += ":" + previous.stdout.strip()
        run("patchelf", "--force-rpath", "--set-rpath", rpath, path)
    for package in packages:
        notice = Path("/usr/share/doc") / package / "copyright"
        if notice.exists():
            shutil.copy2(notice, notices / f"{package}.copyright")
    (notices / "system-packages.txt").write_text(run("dpkg-query", "-W", *sorted(packages)) + "\n")


def main() -> None:
    """安装到独立可搬移 Python，组装完整 server wheel 和真实 Remotion 依赖，再归档。"""
    destination = ROOT / "client/src-tauri"
    with tempfile.TemporaryDirectory(prefix="imv-bundle-") as temporary:
        work = Path(temporary)
        runtime = work / "runtime"
        runtime.mkdir()
        run("uv", "python", "install", "3.12")
        python = Path(run("uv", "python", "find", "--managed-python", "3.12")).resolve()
        python_source = python.parent if sys.platform == "win32" else python.parent.parent
        shutil.copytree(python_source, runtime / "python", symlinks=sys.platform != "win32")
        bundled_python = runtime / ("python/python.exe" if sys.platform == "win32" else "python/bin/python3")
        requirements = work / "requirements.txt"
        run("uv", "export", "--locked", "--project", ROOT / "server", "--no-dev", "--no-emit-project", "--output-file", requirements)
        # 后续会修补 ELF；复制依赖而非硬链接，避免改变 uv 缓存或原解释器。
        run("uv", "pip", "install", "--link-mode=copy", "--break-system-packages", "--python", bundled_python, "--requirements", requirements)
        run("uv", "build", "--project", ROOT / "server", "--wheel", "--out-dir", work / "wheels")
        run("uv", "pip", "install", "--link-mode=copy", "--break-system-packages", "--python", bundled_python, "--no-deps", next((work / "wheels").glob("*.whl")))
        shutil.copy2(ROOT / "server/.env.example", runtime / ".env.example")
        shutil.copy2(ROOT / "LICENSE.md", runtime / "LICENSE.md")
        # Bun 同时下载的 musl 可选二进制不会在本包支持的 glibc 系统使用。
        shutil.copytree(ROOT / "server/src/server/remotion", runtime / "renderer", ignore=shutil.ignore_patterns(".cache", ".data", "__pycache__", "*-musl"))
        if sys.platform == "linux":
            shutil.copytree(Path(sys.argv[1]).resolve().parent, runtime / "chrome", symlinks=True)
            (runtime / "bin").mkdir()
            for tool in ("node", "bun", "uv", "ffmpeg", "ffprobe", "bwrap", "prlimit", "mysqld"):
                executable = (str(Path(os.environ["IMV_FFMPEG_DIR"]) / "bin" / tool) if tool in ("ffmpeg", "ffprobe") else shutil.which(tool)) or ("/usr/sbin/mysqld" if tool == "mysqld" else None)
                if not executable:
                    raise RuntimeError(f"Missing runtime tool: {tool}")
                shutil.copy2(Path(executable).resolve(), runtime / "bin" / tool)
            shutil.copytree("/usr/share/mysql", runtime / "share/mysql")
            shutil.copytree("/usr/lib/mysql/plugin", runtime / "mysql-plugins")
            (runtime / "fonts").mkdir()
            for weight in ("Regular", "Bold"):
                name = f"NotoSansCJK-{weight}.ttc"
                shutil.copy2(Path("/usr/share/fonts/opentype/noto") / name, runtime / "fonts" / name)
            shared_libraries(runtime)
        else:
            from bundle_native import collect

            collect(runtime, work, Path(sys.argv[1]).resolve(), python_source)
        shutil.copytree(Path(os.environ["IMV_FFMPEG_DIR"]) / "licenses", runtime / "licenses/ffmpeg")
        node_parent = Path(shutil.which("node")).resolve().parent
        node_license = node_parent / "LICENSE" if sys.platform == "win32" else node_parent.parent / "LICENSE"
        if node_license.exists():
            shutil.copy2(node_license, runtime / "licenses/node-LICENSE")
        with tarfile.open(destination / "backend.tar", "w", dereference=sys.platform == "win32") as archive:
            archive.add(runtime, arcname=".")
        with (destination / "backend.tar").open("rb") as source:
            identifier = hashlib.file_digest(source, "sha256").hexdigest()
        (destination / "backend.id").write_text(identifier + "\n")
        bundle = {"resources": ["backend.tar", "backend.id"]}
        if sys.platform == "darwin":
            bundle["macOS"] = {"minimumSystemVersion": "15.0"}
        (destination / "tauri.debug.conf.json").write_text(json.dumps({"bundle": bundle}))
        print(f"Bundled backend: {(destination / 'backend.tar').stat().st_size // 1024**2} MiB before installer compression")


if __name__ == "__main__":
    main()
