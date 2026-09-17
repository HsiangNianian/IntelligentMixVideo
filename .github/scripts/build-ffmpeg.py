"""从 FFmpeg 官方 GitHub 最新稳定 tag 构建同源 ffmpeg/ffprobe；不采用 nightly 或第三方二进制。"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request


def latest_stable_tag(tags: list[dict]) -> dict:
    """排除开发版/预发布 tag，按版本而非 API 排序选择稳定版及其准确提交。"""
    releases = [tag for tag in tags if re.fullmatch(r"n\d+\.\d+(?:\.\d+)?", tag["name"])]
    return max(releases, key=lambda tag: tuple(map(int, tag["name"][1:].split("."))))


def resolve_release() -> dict:
    """每轮构建查询官方稳定 tag；缓存仅按解析出的源码提交复用。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "IMV-debug-CI"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com/repos/FFmpeg/FFmpeg/tags?per_page=100", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        tags = json.load(response)
    return latest_stable_tag(tags)


def main() -> None:
    """解析稳定版本并固定提交下载，原生编译自包含工具，将出处/许可证随包保留。"""
    root = Path(os.environ["RUNNER_TEMP"]) / "imv-ffmpeg"
    if sys.argv[1:] == ["--resolve"]:
        latest = resolve_release()
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"commit={latest['commit']['sha']}\n")
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as environment:
            environment.write(f"IMV_FFMPEG_DIR={root / 'install'}\nIMV_FFMPEG_TAG={latest['name']}\nIMV_FFMPEG_COMMIT={latest['commit']['sha']}\n")
        print(f"Latest official stable FFmpeg: {latest['name']} ({latest['commit']['sha']})")
        return
    latest = {"name": os.environ["IMV_FFMPEG_TAG"], "commit": {"sha": os.environ["IMV_FFMPEG_COMMIT"]}}
    root.mkdir(exist_ok=True)
    archive = root / "source.tar.gz"
    url = f"https://github.com/FFmpeg/FFmpeg/archive/{latest['commit']['sha']}.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    with tarfile.open(archive) as source:
        source.extractall(root, filter="data")
    source = next(root.glob("FFmpeg-*"))
    (source / "VERSION").write_text(latest["name"][1:] + "\n")
    destination = root / "install"
    prefix = destination.as_posix()
    if sys.platform == "win32":
        prefix = subprocess.check_output(["cygpath", "-u", str(destination)], text=True).strip()
    configure = [shutil.which("bash") or "bash", "./configure", f"--prefix={prefix}", "--disable-doc", "--disable-debug", "--disable-autodetect", "--disable-ffplay", "--disable-shared", "--enable-static"]
    if sys.platform == "win32":
        configure.extend(["--target-os=mingw32", "--extra-ldflags=-static"])
    # CI 工具链执行官方 configure 脚本；prefix 是独立参数，不经 bash -c 解释。
    subprocess.run(configure, cwd=source, check=True, shell=False)  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-tainted-env-args
    subprocess.run(["make", f"-j{os.cpu_count() or 2}"], cwd=source, check=True)
    subprocess.run(["make", "install"], cwd=source, check=True)
    # 源码 tag 归档没有 VERSION 文件；记录 tag、commit、URL 和二进制实际版本。
    notices = destination / "licenses"
    notices.mkdir()
    for license in source.glob("COPYING*"):
        shutil.copy2(license, notices / license.name)
    suffix = ".exe" if sys.platform == "win32" else ""
    # 只运行本次 CI 编译出的固定 ffmpeg 文件；RUNNER_TEMP 是构建机提供的目录。
    version = subprocess.check_output([str(destination / "bin" / ("ffmpeg" + suffix)), "-version"], text=True, shell=False)  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-tainted-env-args
    (notices / "source.json").write_text(json.dumps({"tag": latest["name"], "commit": latest["commit"]["sha"], "url": url, "version": version}, indent=2))
    print(f"Built official FFmpeg {latest['name']} ({latest['commit']['sha']})")


if __name__ == "__main__":
    main()
