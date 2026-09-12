"""Verify uv keeps the project lock usable despite a user-level mirror override."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_locked_dependencies_override_user_default_mirror(tmp_path: Path) -> None:
    """Check the real lock offline with an empty cache and a conflicting default index."""
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
