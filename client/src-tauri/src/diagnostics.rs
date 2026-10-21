//! 调试日志：以 `--debug` 启动时把页面转发的报错输出到终端，Windows 另开一个控制台窗口，
//! Linux 同时打开 GStreamer 警告，便于观察视频无法播放的原因。未带参数时命令不输出任何内容。

use std::sync::OnceLock;
use std::time::Instant;

static ENABLED: OnceLock<bool> = OnceLock::new();
static START: OnceLock<Instant> = OnceLock::new();

/// 参数里（不含程序路径）有完整的 `--debug` 才开启；`--debugger` 等不算。
fn requested(mut args: impl Iterator<Item = String>) -> bool {
    args.next();
    args.any(|arg| arg == "--debug")
}

/// 本次运行是否开启调试日志；只读取一次命令行。
pub fn enabled() -> bool {
    *ENABLED.get_or_init(|| requested(std::env::args()))
}

/// 在 Tauri 初始化与创建线程之前调用：准备输出位置并打印运行环境。
/// Linux 只在用户没有显式设置时把 `GST_DEBUG` 设为 2（错误与警告），WebKit 的媒体进程继承该环境。
pub fn prepare() {
    if !enabled() {
        return;
    }
    START.get_or_init(Instant::now);
    #[cfg(windows)]
    open_console();
    #[cfg(target_os = "linux")]
    if std::env::var_os("GST_DEBUG").is_none() {
        std::env::set_var("GST_DEBUG", "2");
    }
    print(
        "信息",
        "环境",
        &format!(
            "IntelligentMixVideo {}（{} {}），WebView {}",
            env!("CARGO_PKG_VERSION"),
            std::env::consts::OS,
            std::env::consts::ARCH,
            tauri::webview_version().unwrap_or_else(|error| format!("未知：{error}"))
        ),
    );
}

/// 发布版 Windows 程序没有控制台，新开一个窗口并把标准输出、错误输出指向它。
/// 已有控制台（开发构建）时 `AllocConsole` 失败，继续使用原控制台。关闭该窗口会结束客户端。
#[cfg(windows)]
fn open_console() {
    #[link(name = "kernel32")]
    extern "system" {
        fn AllocConsole() -> i32;
        fn SetConsoleTitleW(title: *const u16) -> i32;
    }
    let title: Vec<u16> = "IntelligentMixVideo 调试日志"
        .encode_utf16()
        .chain([0])
        .collect();
    // SAFETY: 两个系统调用都不保留指针；标题是以 0 结尾的 UTF-16 缓冲，调用期间有效。
    unsafe {
        if AllocConsole() != 0 {
            SetConsoleTitleW(title.as_ptr());
        }
    }
}

/// 一条记录写到标准错误：启动后的秒数、级别、来源与内容，内容可以多行（如调用栈）。
fn print(level: &str, source: &str, message: &str) {
    let elapsed = START
        .get()
        .map_or(0.0, |start| start.elapsed().as_secs_f64());
    eprintln!("[+{elapsed:9.3}s] {level} {source}：{message}");
}

/// 页面询问是否需要转发报错。
#[tauri::command]
pub fn diagnostics_enabled() -> bool {
    enabled()
}

/// 输出页面转发的一条记录；未开启调试日志时忽略。
#[tauri::command]
pub fn diagnostics_log(level: String, source: String, message: String) {
    if enabled() {
        print(&level, &source, &message);
    }
}

#[cfg(test)]
mod tests {
    use super::requested;

    /// 只认程序路径之后完整的 `--debug`，程序路径本身或相近参数都不开启。
    #[test]
    fn debug_flag_is_exact() {
        let args = |values: &[&str]| {
            values
                .iter()
                .map(|value| value.to_string())
                .collect::<Vec<_>>()
                .into_iter()
        };
        assert!(requested(args(&["client", "--debug"])));
        assert!(requested(args(&["client", "--other", "--debug"])));
        assert!(!requested(args(&["client"])));
        assert!(!requested(args(&["--debug"])));
        assert!(!requested(args(&[
            "client",
            "--debugger",
            "debug",
            "-debug"
        ])));
    }
}
