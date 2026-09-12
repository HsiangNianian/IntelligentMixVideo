//! 构建脚本委托 tauri-build 读取配置并生成桌面应用所需的资源信息。

/// 在 Cargo 构建阶段生成 Tauri 上下文依赖。
fn main() {
    tauri_build::build()
}
