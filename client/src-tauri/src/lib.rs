//! 桌面入口：Windows 打包页面通过 localhost 加载；Linux 按 WebKit 版本隔离网页数据。

#[cfg(windows)]
mod localhost;

mod settings;
mod templates;

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
        .invoke_handler(tauri::generate_handler![
            templates::local_templates,
            settings::local_settings
        ])
        .setup(|app| {
            #[cfg(windows)]
            if !tauri::is_dev() {
                let mut window = app.config().app.windows[0].clone();
                window.url = tauri::WebviewUrl::External(localhost::start(app.asset_resolver())?);
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
        .run(context)
        .expect("error while running IntelligentMixVideo");
}
