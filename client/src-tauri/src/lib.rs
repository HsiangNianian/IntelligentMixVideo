//! 桌面入口：Windows 页面使用 localhost，Linux 隔离网页缓存，预览视频经回环媒体服务以 HTTP 播放，测试包按需监督内置服务，设置页可打开调试日志窗口（Windows 另记录 WebView2 子进程退出）。

#[cfg(windows)]
mod localhost;

mod backend;
mod diagnostics;
mod file_lock;
mod media_server;
mod preview_videos;
mod remotion_sessions;
mod settings;
mod updates;

/// Windows 只为当前窗口的准确资源 URL 授权，其他 localhost 端口和远程域名均不匹配。
#[cfg(any(windows, test))]
fn localhost_capability(url: &tauri::Url, label: &str) -> tauri::ipc::CapabilityBuilder {
    tauri::ipc::CapabilityBuilder::new("windows-local-page")
        .local(false)
        .window(label)
        .remote(url.to_string())
        .permission("desktop-commands")
        .permission("core:event:allow-listen")
        .permission("core:event:allow-unlisten")
}

/// 启动主窗口与事件循环；初始化失败时报告错误。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 只记录启动时刻；日志窗口由设置按钮按需打开，普通启动保持静默。
    diagnostics::prepare();
    let mut context = tauri::generate_context!();
    let updates = updates::attach(&mut context);
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        context.config_mut().app.windows[0].create = false;
    }
    #[cfg(target_os = "linux")]
    let context = {
        let mut context = context;
        // 先确定 WebKit 数据目录再建窗口，避免打开其他版本写入的 IndexedDB。
        context.config_mut().app.windows[0].create = false;
        context
    };
    #[cfg(windows)]
    let context = {
        let mut context = context;
        if !tauri::is_dev() {
            // 等本地资源服务绑定成功再创建窗口，避免先加载 tauri.localhost 触发 SDK 授权失败。
            context.config_mut().app.windows[0].create = false;
        }
        context
    };
    tauri::Builder::default()
        .manage(updates)
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(backend::Backend::default())
        .manage(remotion_sessions::Sessions::default())
        .invoke_handler(tauri::generate_handler![
            settings::local_settings,
            preview_videos::preview_video_list,
            preview_videos::preview_video_write,
            media_server::preview_media_base,
            preview_videos::preview_video_remove,
            remotion_sessions::remotion_sessions,
            backend::start_backend,
            updates::client_update,
            diagnostics::diagnostics_open,
            diagnostics::diagnostics_log
        ])
        .setup(|app| {
            {
                use tauri::Manager;
                // 预览视频（内置示例与本机文件）经回环 HTTP 交给 IMS SDK，不用 blob URL；绑定失败与 Windows 页面服务一样阻止启动。
                let directory = preview_videos::directory(app.handle())?;
                let server = media_server::start(media_server::Sources {
                    sample: Box::new(media_server::samples(app.asset_resolver())),
                    local: Box::new(move |file| preview_videos::open(&directory, file)),
                })?;
                app.manage(server);
            }
            #[cfg(windows)]
            {
                use tauri::Manager;
                if !tauri::is_dev() {
                    let mut window = app.config().app.windows[0].clone();
                    let url = localhost::start(app.asset_resolver())?;
                    // 只授权本次绑定的精确 localhost 端口，不向其他本地或远程页面开放 IPC。
                    app.add_capability(localhost_capability(&url, &window.label))?;
                    window.url = tauri::WebviewUrl::External(url);
                    tauri::WebviewWindowBuilder::from_config(app, &window)?.build()?;
                }
                // 开发模式由配置创建窗口；两种方式到这里主窗口都已存在，订阅它的 WebView2 子进程退出。
                if let Some(window) = app.get_webview_window(&app.config().app.windows[0].label) {
                    diagnostics::watch_webview(&window);
                }
            }
            #[cfg(target_os = "linux")]
            {
                use tauri::Manager;
                // 网页缓存按运行时版本隔离；其他本地数据仍保存在原 app_data_dir。
                let data_directory = app
                    .path()
                    .app_data_dir()?
                    .join("webview")
                    .join(tauri::webview_version()?);
                tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                    .data_directory(data_directory)
                    .on_web_resource_request(|_, response| {
                        response.headers_mut().insert(
                            tauri::http::header::CACHE_CONTROL,
                            "no-store".parse().unwrap(),
                        );
                    })
                    .build()?;
            }
            #[cfg(not(any(windows, target_os = "linux")))]
            {
                use tauri::Manager;
                tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                    .on_web_resource_request(|_, response| {
                        response.headers_mut().insert(
                            tauri::http::header::CACHE_CONTROL,
                            "no-store".parse().unwrap(),
                        );
                    })
                    .build()?;
            }
            Ok(())
        })
        .build(context)
        .expect("error while running IntelligentMixVideo")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                backend::shutdown(app);
            }
        });
}

#[cfg(test)]
mod tests {
    use tauri::Manager;

    /// 隔离真实数据库启动，只检查 Tauri 在调用命令前执行的来源权限。
    #[tauri::command]
    fn start_backend() -> bool {
        true
    }

    /// 隔离真实设置文件读写，仅检查命令来源权限。
    #[tauri::command]
    fn local_settings() -> bool {
        true
    }

    /// 不打开真实终端，仅检查调试日志命令同样只对绑定页面开放。
    #[tauri::command]
    fn diagnostics_open() -> bool {
        true
    }

    /// 不向测试输出写日志，仅检查日志转发命令同样只对绑定页面开放。
    #[tauri::command]
    fn diagnostics_log() -> bool {
        true
    }

    /// 用真实权限清单验证准确 URL 可调用，其他端口、域名和窗口均被拒绝。
    #[test]
    fn localhost_ipc_is_limited_to_the_bound_page() {
        let mut context = tauri::generate_context!(test = true);
        context.config_mut().app.windows.clear();
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![
                start_backend,
                local_settings,
                diagnostics_open,
                diagnostics_log
            ])
            .build(context)
            .unwrap();
        let url = "http://localhost:23456/".parse().unwrap();
        app.add_capability(super::localhost_capability(&url, "main"))
            .unwrap();
        for label in ["main", "other"] {
            let view = tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                .build()
                .unwrap();
            for origin in [
                "http://localhost:23456/",
                "http://localhost:23457/",
                "https://example.com/",
            ] {
                for command in [
                    "start_backend",
                    "local_settings",
                    "diagnostics_open",
                    "diagnostics_log",
                    "plugin:event|listen",
                ] {
                    let result = tauri::test::get_ipc_response(
                        &view,
                        tauri::webview::InvokeRequest {
                            cmd: command.into(),
                            callback: tauri::ipc::CallbackFn(0),
                            error: tauri::ipc::CallbackFn(1),
                            url: origin.parse().unwrap(),
                            body: if command == "plugin:event|listen" {
                                tauri::ipc::InvokeBody::Json(
                                    serde_json::json!({"event":"client-update-progress","target":{"kind":"Any"},"handler":0}),
                                )
                            } else {
                                tauri::ipc::InvokeBody::default()
                            },
                            headers: Default::default(),
                            invoke_key: tauri::test::INVOKE_KEY.into(),
                        },
                    );
                    assert_eq!(
                        result.is_ok(),
                        label == "main" && origin == url.as_str(),
                        "{command}: {label}: {origin}"
                    );
                }
            }
        }
    }
}
