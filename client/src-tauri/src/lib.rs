//! 各平台安装包通过官方 localhost 插件加载内嵌前端；开发模式沿用 Vite。

/// 安装包统一使用 localhost:9527，避免 SDK 对 Tauri 默认来源的授权校验失败。
fn localhost(
    window: &mut tauri::utils::config::WindowConfig,
) -> tauri::plugin::TauriPlugin<tauri::Wry> {
    window.url = tauri::WebviewUrl::External("http://localhost:9527".parse().unwrap());
    tauri_plugin_localhost::Builder::new(9527).build()
}

/// 注册官方页面服务并进入桌面事件循环；开发模式由 Vite 提供页面。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut context = tauri::generate_context!();
    let mut builder = tauri::Builder::default();
    if !tauri::is_dev() {
        builder = builder.plugin(localhost(&mut context.config_mut().app.windows[0]));
    }
    builder
        .run(context)
        .expect("error while running IntelligentMixVideo");
}

#[cfg(test)]
mod tests {
    #[test]
    fn packaged_window_uses_localhost() {
        let mut window = tauri::utils::config::WindowConfig::default();
        let original = window.clone();
        let _plugin = super::localhost(&mut window);
        assert_eq!(
            window.url,
            tauri::WebviewUrl::External("http://localhost:9527".parse().unwrap())
        );
        assert_eq!(window.width, original.width);
        assert_eq!(window.title, original.title);
    }
}
