//! 读取当前 WebView 合成后的可视区域（包含隔离的字效 iframe），内存 PNG 由前端裁成模板封面。

type Reply = tokio::sync::oneshot::Sender<Result<Vec<u8>, String>>;

/// 仅主窗口可以请求自己的视图；原生截图和裁剪结果都不写用户磁盘。
#[tauri::command]
pub async fn preview_snapshot(
    window: tauri::WebviewWindow,
    document_size: Option<[u32; 2]>,
) -> Result<tauri::ipc::Response, String> {
    if window.label() != "main" {
        return Err("仅主窗口可截取模板预览".into());
    }
    let (reply, receiver) = tokio::sync::oneshot::channel();
    window
        .with_webview(move |view| snapshot(view, reply, document_size))
        .map_err(|e| e.to_string())?;
    let bytes = tokio::time::timeout(std::time::Duration::from_secs(10), receiver)
        .await
        .map_err(|_| "模板预览截图超时")?
        .map_err(|_| "模板预览截图未完成")??;
    Ok(tauri::ipc::Response::new(bytes))
}

/// WebKitGTK 读取已合成的可见 surface。
#[cfg(target_os = "linux")]
fn snapshot(view: tauri::webview::PlatformWebview, reply: Reply, document_size: Option<[u32; 2]>) {
    use webkit2gtk::{SnapshotOptions, SnapshotRegion, WebViewExt};
    view.inner().snapshot(
        if document_size.is_some() {
            SnapshotRegion::FullDocument
        } else {
            SnapshotRegion::Visible
        },
        SnapshotOptions::empty(),
        None::<&webkit2gtk::gio::Cancellable>,
        move |result| {
            let _ = reply.send(result.map_err(|e| e.to_string()).and_then(|surface| {
                let mut png = Vec::new();
                surface.write_to_png(&mut png).map_err(|e| e.to_string())?;
                Ok(png)
            }));
        },
    );
}

/// WebView2 通过固定的 Page.captureScreenshot 方法读取合成器画面。
#[cfg(windows)]
fn snapshot(view: tauri::webview::PlatformWebview, reply: Reply, document_size: Option<[u32; 2]>) {
    use base64::Engine;
    use webview2_com::CallDevToolsProtocolMethodCompletedHandler;
    use windows_core::{w, HSTRING};
    let options = document_size.map_or_else(|| "{}".to_string(), |[width, height]| {
        serde_json::json!({"captureBeyondViewport":true,"clip":{"x":0,"y":0,"width":width,"height":height,"scale":1}}).to_string()
    });
    let handler =
        CallDevToolsProtocolMethodCompletedHandler::create(Box::new(move |status, json| {
            let result = status.map_err(|e| e.to_string()).and_then(|_| {
                let value: serde_json::Value =
                    serde_json::from_str(&json).map_err(|e| e.to_string())?;
                base64::engine::general_purpose::STANDARD
                    .decode(value["data"].as_str().ok_or("截图缺少数据")?)
                    .map_err(|e| e.to_string())
            });
            let _ = reply.send(result);
            Ok(())
        }));
    // Tauri 在 WebView 的 UI 线程执行本闭包，WebView2 持有完成回调直至返回。
    unsafe {
        let _ = view.controller().CoreWebView2().and_then(|core| {
            core.CallDevToolsProtocolMethod(
                w!("Page.captureScreenshot"),
                &HSTRING::from(options.as_str()),
                &handler,
            )
        });
    }
}

/// WKWebView 在主线程回调 NSImage，再将其编码成内存 PNG。
#[cfg(target_os = "macos")]
fn snapshot(view: tauri::webview::PlatformWebview, reply: Reply, _document_size: Option<[u32; 2]>) {
    use block2::RcBlock;
    use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage};
    use objc2_foundation::{NSDictionary, NSError};
    use objc2_web_kit::WKWebView;
    let reply = std::cell::Cell::new(Some(reply));
    let block = RcBlock::new(move |image: *mut NSImage, _: *mut NSError| {
        // WKWebView 拥有回调参数，转换在本次回调内完成。
        let bytes = unsafe {
            image
                .as_ref()
                .and_then(|i| i.TIFFRepresentation())
                .and_then(|data| NSBitmapImageRep::imageRepWithData(&data))
                .and_then(|rep| {
                    rep.representationUsingType_properties(
                        NSBitmapImageFileType::PNG,
                        &NSDictionary::new(),
                    )
                })
        }
        .map(|data| data.to_vec())
        .ok_or_else(|| "模板预览截图失败".to_string());
        if let Some(sender) = reply.take() {
            let _ = sender.send(bytes);
        }
    });
    unsafe {
        (&*view.inner().cast::<WKWebView>())
            .takeSnapshotWithConfiguration_completionHandler(None, &block);
    }
}
