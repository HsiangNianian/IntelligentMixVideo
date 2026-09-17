"""从最终 MSI/DMG 提取后端，验证随包工具和真实 API 生命周期；仅在对应平台 CI 调用。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    """不借用待打包目录；挂载/解包失败或关键接口测试失败均阻止上传安装包。"""
    installers = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="imv-native-smoke-") as temporary:
        # macOS 的 /var 是软链接；Tauri 拒绝从含软链接的路径解析应用资源。
        work = Path(temporary).resolve()
        extracted = work / "installer"
        extracted.mkdir()
        mounted = False
        try:
            if sys.platform == "darwin":
                subprocess.run(["hdiutil", "attach", str(next(installers.glob("dmg/*.dmg"))), "-readonly", "-nobrowse", "-mountpoint", str(extracted)], check=True, timeout=120)
                mounted = True
                executable = next(extracted.glob("*.app/Contents/MacOS/client"))
            else:
                package = next(installers.glob("msi/*.msi"))
                result = subprocess.run(["msiexec", "/a", str(package), "/qn", f"TARGETDIR={extracted}", "/L*v", str(work / "msi.log")], timeout=180)
                if result.returncode not in (0, 3010):
                    raise RuntimeError((work / "msi.log").read_text(encoding="utf-16", errors="replace"))
                executable = next(extracted.rglob("client.exe"))
            archive = next(extracted.rglob("backend.tar"))
            runtime = work / "runtime"
            runtime.mkdir()
            # 与真实客户端相同的系统 tar；Windows 归档不包含需要特权的符号链接。
            subprocess.run(["tar", "-xf", str(archive), "-C", str(runtime)], check=True, timeout=180)
            subprocess.run(
                ["uv", "run", "--locked", "--project", "server", "pytest", "server/tests/test_desktop.py", "-k", "bundle", "-v"],
                cwd=ROOT, env={**os.environ, "IMV_TEST_BUNDLE": str(runtime), "IMV_TEST_DESKTOP_EXECUTABLE": str(executable)},
                check=True, timeout=720,
            )
        finally:
            if mounted:
                subprocess.run(["hdiutil", "detach", str(extracted)], check=True, timeout=60)


if __name__ == "__main__":
    main()
