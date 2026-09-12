//! 桌面入口：Windows 打包页面通过 localhost 加载，其他环境沿用 Tauri 默认窗口。

#[cfg(windows)]
mod localhost;

/// 启动主窗口与事件循环；初始化失败时报告错误。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let context = tauri::generate_context!();
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
        .setup(|app| {
            #[cfg(windows)]
            if !tauri::is_dev() {
                let mut window = app.config().app.windows[0].clone();
                window.url = tauri::WebviewUrl::External(localhost::start(app.asset_resolver())?);
                tauri::WebviewWindowBuilder::from_config(app, &window)?.build()?;
            }
            #[cfg(not(windows))]
            let _ = app;
            Ok(())
        })
        .run(context)
        .expect("error while running IntelligentMixVideo");
}
