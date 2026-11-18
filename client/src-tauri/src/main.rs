//! 原生程序入口将启动交给共享运行模块，发布版 Windows 隐藏控制台。

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Linux 视频环境的默认值：绕过 DMA-BUF 纹理映射问题，用 playbin3 避免重播后持续等待缓冲。
/// 不设置 GST_DEBUG：调试信息只在调试模式里收集，普通启动不额外输出。
#[cfg(target_os = "linux")]
const VIDEO_ENVIRONMENT: [(&str, &str); 2] = [
    ("WEBKIT_GST_DMABUF_SINK_DISABLED", "1"),
    ("WEBKIT_GST_USE_PLAYBIN3", "1"),
];

/// NVIDIA EGL 在 AppImage 的 WebKit DMA-BUF 渲染路径上可能以 SIGFPE 终止网页进程。
#[cfg(target_os = "linux")]
const DMABUF_RENDERER: &str = "WEBKIT_DISABLE_DMABUF_RENDERER";

/// 写入上述默认值，保留显式环境配置；必须在 Tauri 初始化及创建线程之前调用。
#[cfg(target_os = "linux")]
fn configure_video_environment(nvidia_appimage: bool) {
    for (key, value) in VIDEO_ENVIRONMENT
        .into_iter()
        .chain(nvidia_appimage.then_some((DMABUF_RENDERER, "1")))
    {
        if std::env::var_os(key).is_none() {
            std::env::set_var(key, value);
        }
    }
}

/// 调用共享入口启动桌面客户端。
fn main() {
    // 保留 debug_CI 的显式显示后端对比入口，覆盖 AppImage hook 强制的 x11。
    #[cfg(target_os = "linux")]
    if let Ok(backend) = std::env::var("IMV_GDK_BACKEND") {
        if matches!(backend.as_str(), "wayland" | "x11") {
            std::env::set_var("GDK_BACKEND", backend);
        }
    }
    #[cfg(target_os = "linux")]
    configure_video_environment(
        std::env::var_os("APPIMAGE").is_some()
            && std::path::Path::new("/sys/module/nvidia").is_dir(),
    );

    client_lib::run()
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::{DMABUF_RENDERER, VIDEO_ENVIRONMENT};

    /// 子进程隔离环境变量，验证默认启用兼容路径并保留用户配置，不启动 GUI。
    #[test]
    fn linux_video_environment() {
        const TEST_KEY: &str = "IMV_TEST_VIDEO_KEY";
        const EXPECTED: &str = "IMV_TEST_VIDEO_EXPECTED";
        if let Some(expected) = std::env::var_os(EXPECTED) {
            let key = std::env::var(TEST_KEY).unwrap();
            super::configure_video_environment(key == DMABUF_RENDERER);
            if key != DMABUF_RENDERER {
                assert_eq!(std::env::var_os(DMABUF_RENDERER), None);
            }
            // 回归 v0.9.12：调试信息只在调试模式里收集，启动时不设置 GST_DEBUG。
            assert_eq!(std::env::var_os("GST_DEBUG"), None);
            assert_eq!(std::env::var_os(&key), Some(expected));
            for (other, default) in VIDEO_ENVIRONMENT
                .into_iter()
                .filter(|(other, _)| *other != key)
            {
                assert_eq!(std::env::var_os(other), Some(default.into()));
            }
            return;
        }

        for (key, default) in VIDEO_ENVIRONMENT
            .into_iter()
            .chain([(DMABUF_RENDERER, "1")])
        {
            for value in [None, Some("0"), Some("1"), Some("")] {
                let mut child = std::process::Command::new(std::env::current_exe().unwrap());
                child.args(["--exact", "tests::linux_video_environment"]);
                for (other, _) in VIDEO_ENVIRONMENT {
                    child.env_remove(other);
                }
                child.env_remove("GST_DEBUG");
                child.env_remove(DMABUF_RENDERER);
                child.env(TEST_KEY, key);
                child.env(EXPECTED, value.unwrap_or(default));
                match value {
                    Some(value) => child.env(key, value),
                    None => child.env_remove(key),
                };
                let output = child.output().unwrap();
                assert!(
                    output.status.success(),
                    "配置 {key}={value:?} 验证失败：{}{}",
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
            }
        }
    }
}
