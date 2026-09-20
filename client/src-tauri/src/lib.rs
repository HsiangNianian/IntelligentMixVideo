//! 桌面入口：Windows 页面使用 localhost，Linux 隔离网页缓存，测试包按需监督内置服务。

#[cfg(windows)]
mod localhost;

mod backend;
mod settings;
mod templates;

/// Windows 只为当前窗口的准确资源 URL 授权，其他 localhost 端口和远程域名均不匹配。
#[cfg(any(windows, test))]
fn localhost_capability(url: &tauri::Url, label: &str) -> tauri::ipc::CapabilityBuilder {
    tauri::ipc::CapabilityBuilder::new("windows-local-page")
        .local(false)
        .window(label)
        .remote(url.to_string())
        .permission("desktop-commands")
}

/// 启动主窗口与事件循环；初始化失败时报告错误。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let context = tauri::generate_context!();
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
        .manage(backend::Backend::default())
        .invoke_handler(tauri::generate_handler![
            templates::local_templates,
            settings::local_settings,
            backend::start_backend
        ])
        .setup(|app| {
            #[cfg(windows)]
            if !tauri::is_dev() {
                use tauri::Manager;
                let mut window = app.config().app.windows[0].clone();
                let url = localhost::start(app.asset_resolver())?;
                // 只授权本次绑定的精确 localhost 端口，不向其他本地或远程页面开放 IPC。
                app.add_capability(localhost_capability(&url, &window.label))?;
                window.url = tauri::WebviewUrl::External(url);
                tauri::WebviewWindowBuilder::from_config(app, &window)?.build()?;
            }
            #[cfg(target_os = "linux")]
            {
                use tauri::Manager;
                // 网页缓存按运行时版本隔离；本地模板仍保存在原 app_data_dir。
                let data_directory = app
                    .path()
                    .app_data_dir()?
                    .join("webview")
                    .join(tauri::webview_version()?);
                tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                    .data_directory(data_directory)
                    .build()?;
            }
            #[cfg(not(any(windows, target_os = "linux")))]
            let _ = app;
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

    /// 用真实权限清单验证准确 URL 可调用，其他端口、域名和窗口均被拒绝。
    #[test]
    fn localhost_ipc_is_limited_to_the_bound_page() {
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows.clear();
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![start_backend, local_settings])
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
                for command in ["start_backend", "local_settings"] {
                    let result = tauri::test::get_ipc_response(
                        &view,
                        tauri::webview::InvokeRequest {
                            cmd: command.into(),
                            callback: tauri::ipc::CallbackFn(0),
                            error: tauri::ipc::CallbackFn(1),
                            url: origin.parse().unwrap(),
                            body: tauri::ipc::InvokeBody::default(),
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
