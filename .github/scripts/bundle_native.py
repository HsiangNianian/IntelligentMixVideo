"""收集 Windows/macOS 原生运行时；由 bundle-backend.py 调用，归档不依赖构建机安装路径。"""

import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile


def run(*args: str | Path) -> str:
    """运行原生工具，失败阻止产生不完整安装包。"""
    # 仅供本模块调用系统构建工具；参数列表不经过 shell，也不接收外部请求。
    return subprocess.check_output([str(arg) for arg in args], text=True, shell=False).strip()  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit


def download(url: str, destination: Path) -> None:
    """只在构建时从发行方 HTTPS 地址下载，客户首次启动不联网安装工具。"""
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def collect(runtime: Path, work: Path, chrome: Path, python_source: Path) -> None:
    """复制对应宿主架构的 MySQL、媒体工具、Chrome 与字体，并记录可搬移浏览器位置。"""
    windows = sys.platform == "win32"
    suffix = ".exe" if windows else ""
    architecture = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    package = "mysql-8.4.8-" + ("winx64.zip" if windows else f"macos15-{architecture}.tar.gz")
    archive = work / package
    download("https://downloads.mysql.com/archives/get/p/23/file/" + package, archive)
    mysql = work / "mysql"
    mysql.mkdir()
    if windows:
        with zipfile.ZipFile(archive) as source:
            source.extractall(mysql)
    else:
        with tarfile.open(archive) as source:
            source.extractall(mysql, filter="data")
    mysql = next(mysql.iterdir())
    (runtime / "bin").mkdir()
    (runtime / "licenses").mkdir()
    origins = {runtime / "python": python_source}
    for tool in ("node", "bun", "uv", "ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if tool in ("ffmpeg", "ffprobe"):
            path = Path(os.environ["IMV_FFMPEG_DIR"]) / "bin" / (tool + suffix)
        if not path:
            raise RuntimeError(f"Missing runtime tool: {tool}")
        source = Path(path).resolve()
        target = runtime / "bin" / (tool + suffix)
        shutil.copy2(source, target)
        origins[target] = source
    target = runtime / "bin" / ("mysqld" + suffix)
    shutil.copy2(mysql / "bin" / target.name, target)
    origins[target] = mysql / "bin" / target.name
    shutil.copytree(mysql / "share", runtime / "share/mysql", symlinks=not windows)
    shutil.copytree(mysql / "lib/plugin", runtime / "mysql-plugins", symlinks=not windows)
    origins[runtime / "mysql-plugins"] = mysql / "lib/plugin"
    for path in mysql.glob("LICENSE*"):
        shutil.copy2(path, runtime / "licenses" / path.name)
    if windows:
        for source in [*(mysql / "bin").glob("*.dll"), *(mysql / "lib").glob("*.dll")]:
            shutil.copy2(source, runtime / "bin" / source.name)
        # App-local VC runtime，不要求客户先安装 Visual Studio 或管理员安装运行库。
        vs = Path(os.environ["ProgramFiles"]) / "Microsoft Visual Studio"
        crts = sorted(vs.glob("*/Enterprise/VC/Redist/MSVC/*/x64/Microsoft.VC143.CRT"))
        if not crts:
            raise RuntimeError("Missing redistributable MSVC x64 runtime")
        for source in crts[-1].glob("*.dll"):
            shutil.copy2(source, runtime / "bin" / source.name)
        shutil.copytree(chrome.parent, runtime / "chrome")
        browser = "chrome/" + chrome.name
    else:
        # setup-chrome 会把 .app 内容展开到工具缓存根目录，目录名不再带 .app 后缀。
        app = next(path for path in chrome.parents if path.name == "Contents").parent
        target = runtime / "chrome/Chrome.app"
        shutil.copytree(app, target, symlinks=True)
        browser = (Path("chrome/Chrome.app") / chrome.relative_to(app)).as_posix()
    import json

    (runtime / "runtime.json").write_text(json.dumps({"browser": browser}))
    (runtime / "fonts").mkdir()
    for weight in ("Regular", "Bold"):
        name = f"NotoSansCJK-{weight}.ttc"
        download(f"https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTC/{name}", runtime / "fonts" / name)
    download("https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE", runtime / "licenses/Noto-LICENSE")
    if not windows:
        macos_libraries(runtime, origins)


def macos_libraries(runtime: Path, origins: dict[Path, Path]) -> None:
    """递归携带非系统 dylib，改用 @loader_path 并重新 ad-hoc 签名；禁止残留 Homebrew 绝对路径。"""
    libraries = runtime / "lib"
    libraries.mkdir()
    magic = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"}
    queue = []
    for path in runtime.rglob("*"):
        if path.is_relative_to(runtime / "chrome"):
            continue  # Chrome 是完整可搬移 .app；保留其 Framework、Helper 和发行签名。
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                if source.read(4) in magic:
                    queue.append(path)
    copied = {}
    # Python 已保持发行目录；外部 dylib 则按来源去重到私有 lib。
    for path in queue:
        copied[path.resolve()] = path
    for path in queue:
        original = path
        for target, source in sorted(origins.items(), key=lambda item: len(str(item[0])), reverse=True):
            if path == target or path.is_relative_to(target):
                original = source / path.relative_to(target) if path != target else source
                break
        if not original.exists():
            # pip 后装的原生模块只存在于目标 Python，不能映射回未安装依赖的源解释器。
            original = path
        loads = run("otool", "-L", path).splitlines()[1:]
        commands = run("otool", "-l", original)
        rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", commands)
        identities = re.findall(r"cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.*?) \(offset", commands)
        changed = False
        for line in loads:
            if not line.startswith("\t"):
                continue  # Universal2 文件会为每种架构重复输出标题。
            name = line.strip().split(" (compatibility version")[0]
            if name in identities:
                continue
            if name.startswith(("/usr/lib/", "/System/Library/")):
                continue
            # @loader_path 与 Python/Chrome 的相对依赖已经可搬移，不改签名。
            if name.startswith("@loader_path/") and (path.parent / name.removeprefix("@loader_path/")).exists():
                continue
            if name.startswith("@executable_path/"):
                continue  # 原发行包的解释器/Chrome Framework 保持完整相对布局。
            if name.startswith("@rpath/"):
                relative = name.removeprefix("@rpath/")
                # 保留发行包内可解析的相对 rpath，避免无必要的增长耗尽 Mach-O header padding。
                if any(base.startswith("@loader_path") and (Path(base.replace("@loader_path", str(path.parent))) / relative).is_file() for base in rpaths):
                    continue
                candidates = [Path(base.replace("@loader_path", str(original.parent)).replace("@executable_path", str(original.parent))) / relative for base in rpaths]
                candidates.append(original.parent / relative)
                dependency = next((candidate.resolve() for candidate in candidates if candidate.exists()), None)
                if dependency is None:
                    raise RuntimeError(f"Unresolved Mach-O dependency: {path}: {name}")
            else:
                dependency = Path(name.replace("@loader_path", str(original.parent)))
                if not dependency.is_absolute():
                    dependency = original.parent / dependency
                dependency = dependency.resolve()
            if dependency == original.resolve():
                continue  # dylib 的 LC_ID_DYLIB 不属于外部依赖。
            if dependency not in copied:
                # Python 与 MySQL 可携带同名但不同版本的库，按原目录隔离，不互相覆盖。
                group = hashlib.sha256(str(dependency.parent).encode()).hexdigest()[:12]
                target = libraries / group / dependency.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(dependency, target)
                origins[target] = dependency
                copied[dependency] = target
                queue.append(target)
            target = copied[dependency]
            path.chmod(path.stat().st_mode | 0o200)
            run("install_name_tool", "-change", name, "@loader_path/" + os.path.relpath(target, path.parent), path)
            changed = True
        if changed:
            run("codesign", "--force", "--sign", "-", path)
