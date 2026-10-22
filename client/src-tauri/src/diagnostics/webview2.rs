//! Windows 调试日志的 WebView2 部分：页面以下的问题（显卡驱动、解码、子进程崩溃）由 WebView2 的 Chromium 进程处理，
//! 页面里记录不到。这里在宿主进程收集两类原生记录，页面白屏或黑屏时也照常写入，窗口打开前进入原生缓冲：
//! - 子进程退出：订阅 ProcessFailed，记录 GPU、渲染、工具等进程的类型、原因、退出码与描述；
//! - Chromium 日志：启动参数让 WebView2 把警告与错误写进日志文件，跟随线程把新增行写进调试日志。
//! 跟随文件与退出说明是平台无关的纯逻辑，在其他平台同样编译以便测试。

use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::PathBuf,
};

/// wry 未指定浏览器参数时使用的默认值（wry 0.57 `webview2/mod.rs`）。设置自定义参数会整体替换它，所以原样保留；
/// 本应用没有开启 autoplay 与代理，wry 不会再追加其他参数。升级 wry 时核对。
#[cfg(windows)]
const WRY_DEFAULT_ARGS: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection";

/// 让 WebView2 把 Chromium 的警告与错误（`--log-level=1`）写进日志文件，并启动跟随线程。
/// 必须在创建窗口之前修改配置；配置已指定浏览器参数时不改动。Chromium 不一定接受 `--log-file`，
/// 所以同时跟随它在 WebView2 数据目录（Tauri 默认的 app_local_data_dir/EBWebView）里的默认日志文件。
#[cfg(windows)]
pub fn log_webview2<R: tauri::Runtime>(context: &mut tauri::Context<R>) {
    let Some(local) = std::env::var_os("LOCALAPPDATA") else {
        return;
    };
    let directory = PathBuf::from(local).join(&context.config().identifier);
    if std::fs::create_dir_all(&directory).is_err() {
        return;
    }
    let window = &mut context.config_mut().app.windows[0];
    if window.additional_browser_args.is_some() {
        return;
    }
    let path = directory.join("webview2.log");
    window.additional_browser_args = Some(format!(
        "{WRY_DEFAULT_ARGS} --enable-logging --log-level=1 \"--log-file={}\"",
        path.display()
    ));
    let profile = directory.join("EBWebView");
    let mut files: Vec<Followed> = [
        path,
        profile.join("chrome_debug.log"),
        profile.join("debug.log"),
    ]
    .into_iter()
    .map(Followed::new)
    .collect();
    let _ = std::thread::Builder::new()
        .name("imv-webview2-log".into())
        .spawn(move || loop {
            for file in &mut files {
                for line in file.poll() {
                    super::record_native(super::native_level(&line), "WebView2", &line);
                }
            }
            std::thread::sleep(std::time::Duration::from_secs(1));
        });
}

/// 订阅主窗口 WebView2 的 ProcessFailed：GPU 进程崩溃时画面黑屏或卡住，渲染进程退出时页面失效，
/// 这些都只能在宿主进程知道。订阅失败只记一条警告。
#[cfg(windows)]
pub fn watch_webview(window: &tauri::WebviewWindow) {
    let result = window.with_webview(|platform| {
        use webview2_com::ProcessFailedEventHandler;
        let handler = ProcessFailedEventHandler::create(Box::new(|_, args| {
            if let Some(args) = args {
                super::record_native("错误", "WebView2", &describe_failure(&args));
            }
            Ok(())
        }));
        let mut token = 0i64;
        // SAFETY: with_webview 在 WebView2 所在的 UI 线程调用；controller 与事件处理器由 COM 引用计数管理。
        let subscribed = unsafe {
            platform
                .controller()
                .CoreWebView2()
                .and_then(|core| core.add_ProcessFailed(&handler, &mut token))
        };
        if let Err(error) = subscribed {
            super::record_native(
                "警告",
                "WebView2",
                &format!("无法订阅子进程退出事件：{error}"),
            );
        }
    });
    if let Err(error) = result {
        super::record_native("警告", "WebView2", &format!("无法访问 WebView2：{error}"));
    }
}

/// 读取 ProcessFailed 事件参数；较新的接口（ICoreWebView2ProcessFailedEventArgs2）才有原因、退出码与描述。
#[cfg(windows)]
fn describe_failure(
    args: &webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2ProcessFailedEventArgs,
) -> String {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2ProcessFailedEventArgs2, COREWEBVIEW2_PROCESS_FAILED_KIND,
        COREWEBVIEW2_PROCESS_FAILED_REASON,
    };
    use windows_core::{Interface, PWSTR};
    let mut kind = COREWEBVIEW2_PROCESS_FAILED_KIND::default();
    // SAFETY: 只读取事件参数的属性，输出指针都指向本函数的局部变量；描述字符串由 take_pwstr 释放。
    unsafe {
        let _ = args.ProcessFailedKind(&mut kind);
        let Ok(details) = args.cast::<ICoreWebView2ProcessFailedEventArgs2>() else {
            return failure_text(kind.0, None, None, "");
        };
        let mut reason = COREWEBVIEW2_PROCESS_FAILED_REASON::default();
        let mut exit_code = 0i32;
        let mut description = PWSTR::null();
        let has_reason = details.Reason(&mut reason).is_ok();
        let has_exit_code = details.ExitCode(&mut exit_code).is_ok();
        let description = if details.ProcessDescription(&mut description).is_ok() {
            webview2_com::take_pwstr(description)
        } else {
            String::new()
        };
        failure_text(
            kind.0,
            has_reason.then_some(reason.0),
            has_exit_code.then_some(exit_code),
            &description,
        )
    }
}

/// 子进程退出的说明：哪类进程及后果、原因、退出码（附十六进制，便于对照 Windows 状态码，如 0xC0000005 访问冲突）
/// 与 WebView2 给出的进程描述。类型与原因的取值见 COREWEBVIEW2_PROCESS_FAILED_KIND / _REASON。
#[cfg_attr(not(windows), allow(dead_code))]
fn failure_text(
    kind: i32,
    reason: Option<i32>,
    exit_code: Option<i32>,
    description: &str,
) -> String {
    let process = match kind {
        0 => "浏览器主进程退出，整个页面已失效",
        1 => "渲染进程退出，页面已失效",
        2 => "渲染进程无响应",
        3 => "子框架渲染进程退出",
        4 => "工具进程退出",
        5 => "沙箱辅助进程退出",
        6 => "GPU 进程退出，画面可能黑屏或卡住",
        7 | 8 => "插件进程退出",
        _ => "未知子进程退出",
    };
    let mut text = format!("WebView2 {process}");
    if let Some(reason) = reason {
        let reason = match reason {
            0 => "意外退出",
            1 => "无响应",
            2 => "被终止",
            3 => "崩溃",
            4 => "启动失败",
            5 => "内存不足",
            6 => "数据目录被删除",
            _ => "未知原因",
        };
        text.push_str(&format!("；原因：{reason}"));
    }
    if let Some(code) = exit_code {
        text.push_str(&format!("；退出码 {code}（0x{:08X}）", code as u32));
    }
    if !description.is_empty() {
        text.push_str(&format!("；进程：{description}"));
    }
    text
}

/// 跟随一个由其他进程追加写入的日志文件：记住已读位置，只返回新增的完整行；
/// 文件变短（浏览器启动时重新创建）时从头读。
#[cfg_attr(not(windows), allow(dead_code))]
struct Followed {
    path: PathBuf,
    offset: u64,
    partial: Vec<u8>,
}

#[cfg_attr(not(windows), allow(dead_code))]
impl Followed {
    /// 从文件当前末尾开始跟随，上次运行留下的旧内容不重复记录；文件不存在时从头开始。
    fn new(path: PathBuf) -> Self {
        let offset = std::fs::metadata(&path).map_or(0, |metadata| metadata.len());
        Self {
            path,
            offset,
            partial: Vec::new(),
        }
    }

    /// 读取上次之后新增的内容，返回其中非空的完整行；没写完的一行留到下次。文件不存在或读取失败时返回空。
    fn poll(&mut self) -> Vec<String> {
        let Ok(mut file) = File::open(&self.path) else {
            return Vec::new();
        };
        let length = file.metadata().map_or(0, |metadata| metadata.len());
        if length < self.offset {
            self.offset = 0;
            self.partial.clear();
        }
        if length == self.offset || file.seek(SeekFrom::Start(self.offset)).is_err() {
            return Vec::new();
        }
        let mut bytes = Vec::new();
        if file
            .take(length - self.offset)
            .read_to_end(&mut bytes)
            .is_err()
        {
            return Vec::new();
        }
        self.offset += bytes.len() as u64;
        self.partial.extend_from_slice(&bytes);
        let mut lines = Vec::new();
        while let Some(end) = self.partial.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = self.partial.drain(..=end).collect();
            let text = String::from_utf8_lossy(&line).trim_end().to_string();
            if !text.is_empty() {
                lines.push(text);
            }
        }
        lines
    }
}

#[cfg(test)]
mod tests {
    //! 用临时文件验证日志跟随，以及子进程退出说明；执行 cargo test --locked --lib diagnostics::webview2。
    use super::{failure_text, Followed};
    use std::io::Write;

    /// 跳过启动前已有的旧内容，只返回新增的完整行；半行留到写完，文件被重新创建时从头读。
    #[test]
    fn follows_appended_lines() {
        let path = std::env::temp_dir().join(format!("imv-webview2-{}.log", uuid::Uuid::new_v4()));
        std::fs::write(&path, "[1:2:ERROR:old.cc(1)] 上次运行\n").unwrap();
        let mut followed = Followed::new(path.clone());
        assert!(followed.poll().is_empty());
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        write!(
            file,
            "[3:4:WARNING:gpu.cc(9)] 第一行\n\n[5:6:ERROR:media.cc(7)] 半"
        )
        .unwrap();
        assert_eq!(followed.poll(), ["[3:4:WARNING:gpu.cc(9)] 第一行"]);
        writeln!(file, "行").unwrap();
        assert_eq!(followed.poll(), ["[5:6:ERROR:media.cc(7)] 半行"]);
        drop(file);
        std::fs::write(&path, "新\n").unwrap();
        assert_eq!(followed.poll(), ["新"]);
        let _ = std::fs::remove_file(path);
        assert!(followed.poll().is_empty());
    }

    /// GPU 进程崩溃带原因、十进制与十六进制退出码和描述；旧接口没有细节时只说明进程类型。
    #[test]
    fn describes_process_failures() {
        assert_eq!(
            failure_text(6, Some(3), Some(-1073741819), "GPU Process"),
            "WebView2 GPU 进程退出，画面可能黑屏或卡住；原因：崩溃；退出码 -1073741819（0xC0000005）；进程：GPU Process"
        );
        assert_eq!(
            failure_text(1, None, None, ""),
            "WebView2 渲染进程退出，页面已失效"
        );
    }
}
