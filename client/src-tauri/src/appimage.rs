//! AppImage 原路径换包后的桌面名称同步和重启；保留用户自己的环境和启动参数。

use std::{
    ffi::OsString,
    fs, io,
    os::unix::fs::symlink,
    path::Path,
    process::{Child, Command},
};
use tauri::{Manager, Runtime};

/// 只迁移指向本次 AppImage 的发行包默认链接；自定义名称、普通文件和其他安装各自保留。
fn sync_shortcut(desktop: &Path, image: &Path, version: &str) -> io::Result<()> {
    let image = image.canonicalize()?;
    let current = desktop.join(format!("IntelligentMixVideo v{version}"));
    for entry in fs::read_dir(desktop)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let old_version = name.strip_prefix("IntelligentMixVideo v").or_else(|| {
            name.strip_prefix("IntelligentMixVideo_")?
                .strip_suffix("_amd64.AppImage")
        });
        if entry.path() == current
            || !entry.file_type()?.is_symlink()
            || !old_version.is_some_and(|v| semver::Version::parse(v).is_ok())
            || entry.path().canonicalize().ok().as_ref() != Some(&image)
        {
            continue;
        }
        // 先建立新入口再移除旧链接。遇到用户的同名文件不能覆盖。
        match symlink(&image, &current) {
            Ok(()) => (),
            Err(e)
                if e.kind() == io::ErrorKind::AlreadyExists
                    && current.is_symlink()
                    && current.canonicalize()? == image =>
            {
                ()
            }
            Err(e) => return Err(e),
        }
        fs::remove_file(entry.path())?;
    }
    Ok(())
}

/// 桌面入口是可选的；名称同步失败不影响已完成的安装或启动。
pub(crate) fn update_shortcut<R: Runtime>(app: &tauri::AppHandle<R>, image: &Path, version: &str) {
    if let Ok(desktop) = app.path().desktop_dir() {
        if let Err(e) = sync_shortcut(&desktop, image, version) {
            if e.kind() != io::ErrorKind::NotFound {
                eprintln!("无法更新 AppImage 桌面名称：{e}");
            }
        }
    }
}

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

    /// 第一次升级迁移下载文件链接，下一次升级只留下新名字，并保留自定义链接和文件。
    #[test]
    fn migrate_installed_shortcut_only() {
        let dir = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        fs::create_dir(&dir).unwrap();
        let image = dir.join("installed");
        fs::write(&image, b"installed").unwrap();
        let old = dir.join("IntelligentMixVideo_0.9.10_amd64.AppImage");
        symlink(&image, &old).unwrap();
        symlink(&image, dir.join("custom")).unwrap();
        fs::write(dir.join("IntelligentMixVideo v2.0.1"), b"user file").unwrap();
        assert!(sync_shortcut(&dir, &image, "2.0.1").is_err());
        assert!(old.is_symlink());
        for version in ["2.0.2", "2.0.3", "2.0.3"] {
            sync_shortcut(&dir, &image, version).unwrap();
        }
        assert_eq!(
            fs::read_link(dir.join("IntelligentMixVideo v2.0.3")).unwrap(),
            image
        );
        assert!(!old.exists() && !dir.join("IntelligentMixVideo v2.0.2").exists());
        assert!(dir.join("custom").is_symlink());
        assert_eq!(
            fs::read(dir.join("IntelligentMixVideo v2.0.1")).unwrap(),
            b"user file"
        );
        fs::remove_dir_all(dir).unwrap();
    }
}
