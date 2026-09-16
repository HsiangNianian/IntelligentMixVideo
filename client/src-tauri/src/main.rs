//! 原生程序入口将启动交给共享运行模块，发布版 Windows 隐藏控制台。

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Linux 绕过 DMA-BUF 纹理映射问题，并用 playbin3 避免重播后持续等待缓冲。
/// 保留显式环境配置；必须在 Tauri 初始化及创建线程之前调用。
#[cfg(target_os = "linux")]
fn configure_video_environment() {
    for key in ["WEBKIT_GST_DMABUF_SINK_DISABLED", "WEBKIT_GST_USE_PLAYBIN3"] {
        if std::env::var_os(key).is_none() {
            std::env::set_var(key, "1");
        }
    }
}

/// 调用共享入口启动桌面客户端。
fn main() {
    #[cfg(target_os = "linux")]
    configure_video_environment();

    client_lib::run()
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    /// 子进程隔离环境变量，验证默认启用兼容路径及保留用户配置，不启动 GUI。
    #[test]
    fn linux_video_environment() {
        const KEYS: [&str; 2] = ["WEBKIT_GST_DMABUF_SINK_DISABLED", "WEBKIT_GST_USE_PLAYBIN3"];
        const TEST_KEY: &str = "IMV_TEST_VIDEO_KEY";
        const EXPECTED: &str = "IMV_TEST_VIDEO_EXPECTED";
        if let Some(expected) = std::env::var_os(EXPECTED) {
            super::configure_video_environment();
            let key = std::env::var(TEST_KEY).unwrap();
            assert_eq!(std::env::var_os(&key), Some(expected));
            for other in KEYS.into_iter().filter(|other| *other != key) {
                assert_eq!(std::env::var_os(other), Some("1".into()));
            }
            return;
        }

        for key in KEYS {
            for value in [None, Some("0"), Some("1"), Some("")] {
                let mut child = std::process::Command::new(std::env::current_exe().unwrap());
                child.args(["--exact", "tests::linux_video_environment"]);
                for other in KEYS {
                    child.env_remove(other);
                }
                child.env(TEST_KEY, key);
                child.env(EXPECTED, value.unwrap_or("1"));
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
