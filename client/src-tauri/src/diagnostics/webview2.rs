//! Windows 调试日志的 WebView2 部分：页面以下的问题（显卡驱动、解码、子进程崩溃）由 WebView2 的 Chromium 进程处理，
//! 页面里记录不到。这里订阅主窗口的 ProcessFailed，在宿主进程记录 GPU、渲染、工具等子进程退出的类型、原因、退出码与描述，
//! 页面白屏或黑屏时也照常写入，窗口打开前进入原生缓冲。
//! 不打开 Chromium 自身的日志：Windows 上 `--enable-logging` 会给 WebView2 进程弹出控制台窗口，且只能在启动时设置，
//! 无法随调试日志窗口按需开启（v0.9.12 曾默认开启，每次启动都弹）。退出说明是平台无关的纯逻辑，在其他平台同样编译以便测试。

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

#[cfg(test)]
mod tests {
    //! 验证子进程退出说明；执行 cargo test --locked --lib diagnostics::webview2。
    use super::failure_text;

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
