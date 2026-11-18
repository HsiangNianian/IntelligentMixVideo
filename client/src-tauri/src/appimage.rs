//! 从宿主环境重启已安装的 AppImage，保留用户启动参数。

use std::{
    ffi::OsString,
    io,
    path::Path,
    process::{Child, Command},
};

/// 从宿主环境启动新 AppImage，剔除旧挂载的动态库路径；创建失败时调用方继续运行旧窗口。
pub(crate) fn launch(image: &Path, argv: &[OsString]) -> io::Result<Child> {
    let mut command = Command::new(image);
    crate::diagnostics::host_environment(&mut command);
    command.args(argv.iter().skip(1)).spawn()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 只在子测试进程中注入旧挂载，验证新版入口继承用户参数和宿主路径，不加载旧包库。
    #[test]
    fn launch_without_old_mount() {
        const TEST_ENV: &str = "IMV_TEST_APPIMAGE_RELAUNCH";
        if std::env::var_os(TEST_ENV).is_some() {
            let args = ["old.AppImage", "-c",
                r#"test -z "$APPDIR" && test "$LD_LIBRARY_PATH" = "/opt/user-lib" && test "$1" = "two words""#,
                "sh", "two words"].map(OsString::from);
            assert!(launch(Path::new("/bin/sh"), &args)
                .unwrap()
                .wait()
                .unwrap()
                .success());
            return;
        }
        let status = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "appimage::tests::launch_without_old_mount"])
            .env(TEST_ENV, "1")
            .env("APPDIR", "/tmp/.mount_IMVold")
            .env(
                "LD_LIBRARY_PATH",
                "/tmp/.mount_IMVold/usr/lib:/opt/user-lib",
            )
            .status()
            .unwrap();
        assert!(status.success());
    }
}
