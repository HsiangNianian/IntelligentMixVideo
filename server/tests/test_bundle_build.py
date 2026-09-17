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
