//! 桌面运行模块创建默认 Tauri 应用，加载生成配置后进入事件循环。

/// 启动主窗口与事件循环；初始化失败时报告错误。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running IntelligentMixVideo");
}
