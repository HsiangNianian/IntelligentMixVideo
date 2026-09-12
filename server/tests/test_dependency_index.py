"""离线验证项目索引与锁文件来源兼容；在 server/ 执行 uv run --locked pytest -v。

空缓存下仅检查锁文件，不下载依赖，也不验证真实 PyPI 的可用性。
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_locked_dependencies_override_user_default_mirror(tmp_path: Path) -> None:
    """项目 PyPI 覆盖用户默认镜像；删除该覆盖会使锁中的来源失效，离线检查失败。"""
    project = tmp_path / "project"
    project.mkdir()
    server = Path(__file__).resolve().parents[1]
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(server / name, project / name)
    lock_before = (project / "uv.lock").read_bytes()

    config = tmp_path / "config"
    (config / "uv").mkdir(parents=True)
    (config / "uv" / "uv.toml").write_text(
        '[[index]]\nurl = "https://mirror.invalid/simple"\ndefault = true\n',
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("UV_")
    }
    environment.update(
        XDG_CONFIG_HOME=str(config),
        APPDATA=str(config),
        UV_CACHE_DIR=str(tmp_path / "cache"),
        UV_PYTHON_DOWNLOADS="never",
    )
    result = subprocess.run(
        ["uv", "lock", "--check", "--offline", "--python", sys.executable],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "uv.lock").read_bytes() == lock_before
