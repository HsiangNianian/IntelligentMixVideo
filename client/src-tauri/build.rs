//! 生成 Tauri 资源；Windows 应用和测试程序均嵌入 Common Controls v6 清单。

/// 在 Cargo 构建阶段生成 Tauri 上下文依赖。
fn main() {
    let mut attributes = tauri_build::Attributes::new();
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // tauri-build 默认只向主程序链接清单，mock 测试会在加载时缺少 v6 入口。
        // 由 MSVC 为所有链接目标嵌入同一依赖，避免重复的 manifest 资源。
        // https://github.com/tauri-apps/tauri/issues/13419
        attributes = attributes
            .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'");
    }
    tauri_build::try_build(attributes).expect("failed to build Tauri resources");
}
