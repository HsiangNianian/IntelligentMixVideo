//! 调试日志：在设置中打开独立终端窗口，实时显示页面错误、媒体诊断、控件操作和原生日志。
//! 日志同时写入应用数据目录的 `data/diagnostics.log`，终端窗口只负责跟随该文件；
//! 关闭窗口不会结束客户端，再次点击设置中的按钮可重新打开；找不到可用终端时照常写入文件，由页面提示路径。
//! Linux 启动时把标准错误接到管道：WebKit 网页进程继承它，GStreamer 与 GLib 的警告和页面记录写进同一个日志，
//! 窗口打开前只保留最近的部分。

use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
    process::{Child, Command},
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::Manager;

/// 应用启动时刻（Unix 毫秒）；日志时间戳是相对它的秒数，页面记录按自己的发生时刻换算。
static START: OnceLock<f64> = OnceLock::new();
/// 窗口打开前最多缓冲的原生记录条数与字符数，与页面缓冲的上限一致。
const MAX_PENDING_LINES: usize = 400;
const MAX_PENDING_CHARS: usize = 200_000;

/// 日志去向：窗口打开后写文件；打开前只缓冲原生标准错误的最近记录（页面记录由页面自己缓冲）。
struct Sink {
    file: Option<File>,
    pending: VecDeque<String>,
    pending_chars: usize,
}

static SINK: Mutex<Sink> = Mutex::new(Sink {
    file: None,
    pending: VecDeque::new(),
    pending_chars: 0,
});

impl Sink {
    /// 写入一条已格式化的记录；文件未打开时，keep 为真才放进有界缓冲，超出上限先丢最旧的。
    fn write(&mut self, line: String, keep: bool) {
        if let Some(file) = self.file.as_mut() {
            let _ = writeln!(file, "{line}");
            let _ = file.flush();
            return;
        }
        if !keep {
            return;
        }
        self.pending_chars += line.len();
        self.pending.push_back(line);
        while self.pending.len() > MAX_PENDING_LINES || self.pending_chars > MAX_PENDING_CHARS {
            match self.pending.pop_front() {
                Some(old) => self.pending_chars -= old.len(),
                None => break,
            }
        }
    }

    /// 改用新打开的文件，并按发生顺序补写打开前缓冲的记录。
    fn open(&mut self, mut file: File) {
        for line in self.pending.drain(..) {
            let _ = writeln!(file, "{line}");
        }
        self.pending_chars = 0;
        let _ = file.flush();
        self.file = Some(file);
    }
}

/// 当前时刻（Unix 毫秒）。
fn now_ms() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64() * 1000.0)
}

/// 记录应用启动时刻；Linux 另接管标准错误收集原生日志。不打开窗口，普通启动保持静默。
pub fn prepare() {
    START.get_or_init(now_ms);
    #[cfg(target_os = "linux")]
    if let Err(error) = capture_stderr() {
        echo(format!("调试日志无法接管标准错误：{error}\n").as_bytes());
    }
}

/// 一条记录的格式：相对启动的秒数、级别、来源与内容。源和级别中的换行会被压平，消息本身允许多行（例如调用栈）。
fn format_line(at_ms: f64, level: &str, source: &str, message: &str) -> String {
    let elapsed = ((at_ms - START.get().copied().unwrap_or(at_ms)) / 1000.0).max(0.0);
    let level = level.replace(['\r', '\n'], " ");
    let source = source.replace(['\r', '\n'], " ");
    format!("[+{elapsed:9.3}s] {level} {source}：{message}")
}

/// Linux 接管 fd 2 之前的标准错误；程序自己的输出写到这里，避免再被管道读回日志。
#[cfg(target_os = "linux")]
static ORIGINAL_STDERR: Mutex<Option<File>> = Mutex::new(None);

/// 写到原来的标准错误（开发时的终端或系统日志），失败时静默，不能因输出失败中断记录。
fn echo(bytes: &[u8]) {
    #[cfg(target_os = "linux")]
    if let Ok(mut original) = ORIGINAL_STDERR.lock() {
        if let Some(file) = original.as_mut() {
            let _ = file.write_all(bytes);
            return;
        }
    }
    let _ = std::io::stderr().write_all(bytes);
}

/// 输出一条记录到标准错误与已打开的日志文件。
fn print(at_ms: f64, level: &str, source: &str, message: &str) {
    let line = format_line(at_ms, level, source, message);
    echo(format!("{line}\n").as_bytes());
    if let Ok(mut sink) = SINK.lock() {
        sink.write(line, false);
    }
}

/// 原生输出单行的最大字节数；没有换行的超长输出按此长度分段。
#[cfg(target_os = "linux")]
const MAX_NATIVE_LINE: u64 = 16 * 1024;

/// 把本进程的 fd 2 换成管道写端，由读取线程逐行转发回原来的标准错误并写进日志。
/// 子进程（WebKit 网页进程、GStreamer 插件扫描等）继承 fd 2，所以它们的警告也会进入日志。
/// 先启动读取线程再替换 fd 2，避免管道无人读取时写满阻塞；替换失败则线程随管道关闭退出。
#[cfg(target_os = "linux")]
fn capture_stderr() -> std::io::Result<()> {
    use std::os::fd::{AsFd, AsRawFd};
    let (reader, writer) = std::io::pipe()?;
    let original = File::from(std::io::stderr().as_fd().try_clone_to_owned()?);
    std::thread::Builder::new()
        .name("imv-stderr".into())
        .spawn(move || forward(reader))?;
    if let Ok(mut slot) = ORIGINAL_STDERR.lock() {
        *slot = Some(original);
    }
    // SAFETY: dup2 原子地把 fd 2 指向管道写端，不关闭或改动 Rust 持有的其他句柄；writer 随后关闭，fd 2 保留写端。
    if unsafe { libc::dup2(writer.as_raw_fd(), libc::STDERR_FILENO) } < 0 {
        let error = std::io::Error::last_os_error();
        if let Ok(mut slot) = ORIGINAL_STDERR.lock() {
            *slot = None;
        }
        return Err(error);
    }
    Ok(())
}

/// 读取线程：每行原样写回原来的标准错误，再按内容判断级别写进日志（窗口未打开时进缓冲）。
/// 线程不能提前退出，否则 fd 2 的管道写满后所有写标准错误的进程都会阻塞；读取出错时稍后重试。
#[cfg(target_os = "linux")]
fn forward(reader: std::io::PipeReader) {
    use std::io::{BufRead, BufReader, Read};
    let mut reader = BufReader::new(reader);
    let mut line = Vec::new();
    loop {
        line.clear();
        match reader
            .by_ref()
            .take(MAX_NATIVE_LINE)
            .read_until(b'\n', &mut line)
        {
            Ok(0) => return,
            Ok(_) => {}
            Err(_) => {
                std::thread::sleep(std::time::Duration::from_millis(100));
                continue;
            }
        }
        echo(&line);
        let text = String::from_utf8_lossy(&line);
        let text = text.trim_end();
        if text.is_empty() {
            continue;
        }
        let formatted = format_line(now_ms(), native_level(text), "原生", text);
        if let Ok(mut sink) = SINK.lock() {
            sink.write(formatted, true);
        }
    }
}

/// 按原生输出的常见标记判断级别：GStreamer 的 ERROR/WARN 列，GLib 的 CRITICAL/WARNING。
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn native_level(line: &str) -> &'static str {
    if line.contains("ERROR") || line.contains("CRITICAL") {
        "错误"
    } else if line.contains("WARN") {
        "警告"
    } else {
        "信息"
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

/// 在后台等待启动的终端程序退出，避免它（如单实例终端交给已有窗口后立即退出）成为僵尸进程。
#[cfg(unix)]
fn reap(mut child: Child) {
    std::thread::spawn(move || {
        let _ = child.wait();
    });
}

/// 将带单引号的 shell/path 参数安全地放进 shell 脚本。
#[cfg(target_os = "macos")]
fn shell_quote(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "'\\''"))
}

/// Windows 新建一个 PowerShell 控制台跟随日志文件；不附加到 GUI 进程，关闭窗口不会退出客户端。
/// Windows PowerShell 5.1 默认按系统 ANSI 代码页读取无 BOM 文件，须显式按 UTF-8 读取，否则中文乱码。
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
            "$Host.UI.RawUI.WindowTitle='IntelligentMixVideo 调试日志'; Get-Content -LiteralPath $env:IMV_DIAGNOSTICS_LOG -Encoding UTF8 -Wait",
        ])
        .env("IMV_DIAGNOSTICS_LOG", path)
        .creation_flags(CREATE_NEW_CONSOLE)
        .spawn()
        .map(|_: Child| ())
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
        .map(reap)
        .map_err(|error| format!("无法打开 Terminal：{error}"))
}

/// Linux 终端程序与它执行后续命令所需的参数。xdg-terminal-exec 与 x-terminal-emulator 使用用户选定的默认终端，
/// 其余按各桌面的默认终端排列（Fedora 41 起 GNOME 默认是 Ptyxis，GNOME Console 是 kgx）。
#[cfg(all(unix, not(target_os = "macos")))]
const TERMINALS: [(&str, &[&str]); 12] = [
    ("xdg-terminal-exec", &[]),
    ("x-terminal-emulator", &["-e"]),
    ("ptyxis", &["--new-window", "--"]),
    ("kgx", &["--"]),
    ("gnome-terminal", &["--"]),
    ("konsole", &["-e"]),
    ("xfce4-terminal", &["-x"]),
    ("mate-terminal", &["-x"]),
    ("alacritty", &["-e"]),
    ("kitty", &[]),
    ("foot", &[]),
    ("xterm", &["-e"]),
];

/// 从冒号分隔的路径列表里去掉 dir 下的条目；没有剩下的条目时返回 None（应删除该变量）。
#[cfg(all(unix, not(target_os = "macos")))]
fn without_dir(value: &str, dir: &str) -> Option<String> {
    let kept: Vec<&str> = value
        .split(':')
        .filter(|entry| !entry.starts_with(dir))
        .collect();
    kept.iter()
        .any(|entry| !entry.is_empty())
        .then(|| kept.join(":"))
}

/// AppImage 的启动钩子把 GTK、GIO、GSettings、GStreamer 等变量指向包内的库与模块；宿主终端是另一个 GTK 程序，
/// 带着这些变量启动可能加载不兼容的模块而崩溃。去掉指向包内目录（APPDIR）的条目，空了就删除该变量。
#[cfg(all(unix, not(target_os = "macos")))]
fn host_environment(command: &mut Command) {
    let Some(appdir) = std::env::var("APPDIR").ok().filter(|dir| !dir.is_empty()) else {
        return;
    };
    for (key, value) in std::env::vars_os() {
        let Some(value) = value.to_str().filter(|value| value.contains(&appdir)) else {
            continue;
        };
        match without_dir(value, &appdir) {
            Some(kept) => command.env(key, kept),
            None => command.env_remove(key),
        };
    }
}

/// Linux 依次尝试常见终端模拟器；都没有安装时说明尝试过的程序，由调用方提示日志文件位置。
#[cfg(all(unix, not(target_os = "macos")))]
fn open_viewer(path: &Path) -> Result<(), String> {
    let mut missing = Vec::new();
    let mut errors = Vec::new();
    for (program, args) in TERMINALS {
        let mut command = Command::new(program);
        command
            .args(args)
            .args(["tail", "-n", "+1", "-F"])
            .arg(path);
        host_environment(&mut command);
        match command.spawn() {
            Ok(child) => {
                reap(child);
                return Ok(());
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => missing.push(program),
            Err(error) => errors.push(format!("{program}：{error}")),
        }
    }
    if errors.is_empty() {
        Err(format!(
            "无法打开终端窗口：没有找到可用的终端程序（已尝试 {}）",
            missing.join("、")
        ))
    } else {
        Err(format!("无法打开终端窗口：{}", errors.join("；")))
    }
}

/// 打开结果：日志文件路径，以及终端窗口没能打开时的原因；后者不影响写入日志文件。
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Opened {
    path: String,
    viewer_error: Option<String>,
}

/// 设置按钮调用：打开日志文件并补写原生缓冲，再尝试打开终端窗口；
/// 返回后前端开始转发并补发打开前的页面记录。终端打不开时文件照常写入，由页面提示路径。
#[tauri::command]
pub fn diagnostics_open(app: tauri::AppHandle) -> Result<Opened, String> {
    START.get_or_init(now_ms);
    let path = log_path(&app)?;
    let file = open_log(&path)?;
    SINK.lock().map_err(|_| "调试日志状态不可用")?.open(file);
    let viewer_error = open_viewer(&path).err();
    match &viewer_error {
        Some(error) => print(
            now_ms(),
            "警告",
            "调试日志",
            &format!("{error}；日志继续写入：{}", path.display()),
        ),
        None => print(
            now_ms(),
            "信息",
            "调试日志",
            &format!("日志窗口已打开：{}", path.display()),
        ),
    }
    Ok(Opened {
        path: path.display().to_string(),
        viewer_error,
    })
}

/// 输出页面转发的一条记录；at 为页面记录的发生时刻（Unix 毫秒），缺失时取收到的时刻。窗口尚未打开时忽略。
#[tauri::command]
pub fn diagnostics_log(level: String, source: String, message: String, at: Option<f64>) {
    let open = SINK.lock().map(|sink| sink.file.is_some()).unwrap_or(false);
    if open {
        let message: String = message.chars().take(32_000).collect();
        print(at.unwrap_or_else(now_ms), &level, &source, &message);
    }
}

#[cfg(test)]
mod tests {
    use super::{native_level, open_log, Sink, MAX_PENDING_LINES};
    use std::collections::VecDeque;
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

    /// 窗口打开前只保留最近的原生记录，页面记录不缓冲；打开时按顺序补写，之后直接写文件。
    #[test]
    fn buffers_recent_native_lines_until_opened() {
        let mut sink = Sink {
            file: None,
            pending: VecDeque::new(),
            pending_chars: 0,
        };
        sink.write("页面".into(), false);
        for index in 0..MAX_PENDING_LINES + 5 {
            sink.write(format!("原生 {index}"), true);
        }
        assert_eq!(sink.pending.len(), MAX_PENDING_LINES);
        assert_eq!(sink.pending.front().map(String::as_str), Some("原生 5"));
        let path =
            std::env::temp_dir().join(format!("imv-diagnostics-{}.log", uuid::Uuid::new_v4()));
        sink.open(std::fs::File::create(&path).unwrap());
        sink.write("之后".into(), false);
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.first(), Some(&"原生 5"));
        assert_eq!(lines.last(), Some(&"之后"));
        assert_eq!(lines.len(), MAX_PENDING_LINES + 1);
        assert!(sink.pending.is_empty());
        let _ = std::fs::remove_file(path);
    }

    /// GStreamer 与 GLib 输出按级别标记归类。
    #[test]
    fn classifies_native_lines() {
        assert_eq!(
            native_level("0:00:01.2 123 0x1 ERROR vaapi foo.c:1: failed"),
            "错误"
        );
        assert_eq!(
            native_level("(WebKitWebProcess:9): GLib-CRITICAL **: x"),
            "错误"
        );
        assert_eq!(native_level("0:00:01.2 123 0x1 WARN  basesrc x"), "警告");
        assert_eq!(native_level("** (process:1): WARNING **: y"), "警告");
        assert_eq!(native_level("starry engine"), "信息");
    }

    /// 从 AppImage 启动宿主终端时去掉包内路径：列表只删包内条目，全部在包内的变量整体删除。
    #[cfg(all(unix, not(target_os = "macos")))]
    #[test]
    fn strips_appimage_paths_for_host_terminal() {
        use super::without_dir;
        let dir = "/tmp/.mount_IMVabc";
        assert_eq!(
            without_dir(
                "/tmp/.mount_IMVabc/usr/share:/usr/share:/usr/local/share",
                dir
            ),
            Some("/usr/share:/usr/local/share".into())
        );
        assert_eq!(
            without_dir("/tmp/.mount_IMVabc//usr/lib/gtk-3.0", dir),
            None
        );
        assert_eq!(without_dir("/tmp/.mount_IMVabc/usr/share:", dir), None);
    }

    /// 子进程里接管标准错误：本进程与子进程写到 fd 2 的内容都进入原生缓冲，并按级别标记；
    /// 同时原样写回原来的标准错误。在独立进程中运行，避免测试框架自己的输出被接管。
    #[cfg(target_os = "linux")]
    #[test]
    fn captures_child_stderr() {
        const MARKER: &str = "IMV_TEST_CAPTURE_STDERR";
        if std::env::var_os(MARKER).is_some() {
            super::prepare();
            let status = std::process::Command::new("sh")
                .args(["-c", "echo '0:00:00.1 1 0x1 WARN  vaapi 子进程警告' >&2"])
                .status()
                .unwrap();
            assert!(status.success());
            eprintln!("本进程输出");
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            loop {
                let pending: Vec<String> = super::SINK
                    .lock()
                    .unwrap()
                    .pending
                    .iter()
                    .cloned()
                    .collect();
                if pending
                    .iter()
                    .any(|line| line.contains("警告 原生：0:00:00.1 1 0x1 WARN  vaapi 子进程警告"))
                    && pending
                        .iter()
                        .any(|line| line.contains("信息 原生：本进程输出"))
                {
                    return;
                }
                assert!(
                    std::time::Instant::now() < deadline,
                    "未收到标准错误：{pending:?}"
                );
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        }
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "diagnostics::tests::captures_child_stderr",
                "--nocapture",
            ])
            .env(MARKER, "1")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(String::from_utf8_lossy(&output.stderr).contains("子进程警告"));
    }
}
