//! 调试日志：在设置中打开独立终端窗口，实时显示页面错误、媒体诊断和控件操作。
//! 日志同时写入应用数据目录的 `data/diagnostics.log`，终端窗口只负责跟随该文件；
//! 关闭窗口不会结束客户端，再次点击设置中的按钮可重新打开。

use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
    process::Command,
    sync::{Mutex, OnceLock},
    time::Instant,
};
use tauri::Manager;

/// 应用启动时刻用于日志时间戳；日志文件句柄在窗口打开后写入。
static START: OnceLock<Instant> = OnceLock::new();
static LOG: Mutex<Option<File>> = Mutex::new(None);

/// 记录应用启动时刻；不打开窗口、不修改环境变量，普通启动保持静默。
pub fn prepare() {
    START.get_or_init(Instant::now);
}

/// 一条记录写到日志文件与标准错误：启动后的秒数、级别、来源与内容。
/// 源和级别中的换行会被压平，消息本身允许多行（例如调用栈）。
fn print(level: &str, source: &str, message: &str) {
    let elapsed = START
        .get()
        .map_or(0.0, |start| start.elapsed().as_secs_f64());
    let level = level.replace(['\r', '\n'], " ");
    let source = source.replace(['\r', '\n'], " ");
    let line = format!("[+{elapsed:9.3}s] {level} {source}：{message}");
    eprintln!("{line}");
    if let Ok(mut log) = LOG.lock() {
        if let Some(file) = log.as_mut() {
            let _ = writeln!(file, "{line}");
            let _ = file.flush();
        }
    }
}

/// 日志文件位置与应用其他本地数据一致，不读取或接受页面提供的任意路径。
fn log_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let directory = app
        .path()
        .app_data_dir()
        .map_err(|_| "读取应用目录失败")?
        .join("data");
    fs::create_dir_all(&directory).map_err(|_| "创建调试日志目录失败")?;
    Ok(directory.join("diagnostics.log"))
}

/// 打开文件；超过 8 MiB 时从新会话重新写，避免长期运行无限增长。
fn open_log(path: &Path) -> Result<File, String> {
    let rotate = fs::metadata(path)
        .map(|metadata| metadata.len() > 8 * 1024 * 1024)
        .unwrap_or(false);
    if rotate {
        File::create(path).map_err(|error| format!("重置调试日志失败：{error}"))
    } else {
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|error| format!("打开调试日志失败：{error}"))
    }
}

/// 将带单引号的 shell/path 参数安全地放进 shell 脚本。
#[cfg(target_os = "macos")]
fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
}

/// Windows 新建一个 PowerShell 控制台跟随日志文件；不附加到 GUI 进程，关闭窗口不会退出客户端。
#[cfg(windows)]
fn open_viewer(path: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;
    Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NoExit",
            "-Command",
            "$Host.UI.RawUI.WindowTitle='IntelligentMixVideo 调试日志'; Get-Content -LiteralPath $env:IMV_DIAGNOSTICS_LOG -Wait",
        ])
        .env("IMV_DIAGNOSTICS_LOG", path)
        .creation_flags(CREATE_NEW_CONSOLE)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开调试日志窗口：{error}"))
}

/// macOS 用 Terminal 打开一个 `.command` 脚本，持续跟随日志文件。
#[cfg(target_os = "macos")]
fn open_viewer(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    let script = path.with_file_name("diagnostics-viewer.command");
    let source = format!("#!/bin/sh\nexec tail -n +1 -F {}\n", shell_quote(path));
    fs::write(&script, source).map_err(|error| format!("创建调试日志启动脚本失败：{error}"))?;
    fs::set_permissions(&script, fs::Permissions::from_mode(0o700))
        .map_err(|error| format!("设置调试日志启动脚本权限失败：{error}"))?;
    Command::new("open")
        .args(["-a", "Terminal", script.to_string_lossy().as_ref()])
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开 Terminal：{error}"))
}

/// Linux 依次尝试常见终端模拟器；未安装或没有图形会话时返回明确错误。
#[cfg(all(unix, not(target_os = "macos")))]
fn open_viewer(path: &Path) -> Result<(), String> {
    let candidates: [(&str, &[&str]); 4] = [
        ("x-terminal-emulator", &["-e", "tail", "-n", "+1", "-F"]),
        ("gnome-terminal", &["--", "tail", "-n", "+1", "-F"]),
        ("konsole", &["-e", "tail", "-n", "+1", "-F"]),
        ("xterm", &["-e", "tail", "-n", "+1", "-F"]),
    ];
    let mut errors = Vec::new();
    for (program, args) in candidates {
        match Command::new(program).args(args).arg(path).spawn() {
            Ok(_) => return Ok(()),
            Err(error) => errors.push(format!("{program}: {error}")),
        }
    }
    Err(format!("无法打开终端窗口：{}", errors.join("；")))
}

/// 设置按钮调用：准备日志文件并打开终端窗口；返回后前端再补发打开前的缓冲记录。
#[tauri::command]
pub fn diagnostics_open(app: tauri::AppHandle) -> Result<(), String> {
    START.get_or_init(Instant::now);
    let path = log_path(&app)?;
    let file = open_log(&path)?;
    *LOG.lock().map_err(|_| "调试日志状态不可用")? = Some(file);
    open_viewer(&path)?;
    print(
        "信息",
        "调试日志",
        &format!("日志窗口已打开：{}", path.display()),
    );
    Ok(())
}

/// 输出页面转发的一条记录；窗口尚未打开时忽略。
#[tauri::command]
pub fn diagnostics_log(level: String, source: String, message: String) {
    if LOG.lock().map(|log| log.is_some()).unwrap_or(false) {
        let message: String = message.chars().take(32_000).collect();
        print(&level, &source, &message);
    }
}

#[cfg(test)]
mod tests {
    use super::open_log;
    use std::io::Write;

    /// 超过上限的旧日志在下一次打开时截断；普通日志继续追加，窗口重开不会丢本次会话。
    #[test]
    fn rotates_only_oversized_log_file() {
        let path =
            std::env::temp_dir().join(format!("imv-diagnostics-{}.log", uuid::Uuid::new_v4()));
        std::fs::write(&path, "old").unwrap();
        let mut file = open_log(&path).unwrap();
        file.write_all(b"new").unwrap();
        drop(file);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "oldnew");
        std::fs::write(&path, vec![b'x'; 8 * 1024 * 1024 + 1]).unwrap();
        let mut file = open_log(&path).unwrap();
        file.write_all(b"next").unwrap();
        drop(file);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "next");
        let _ = std::fs::remove_file(path);
    }
}
