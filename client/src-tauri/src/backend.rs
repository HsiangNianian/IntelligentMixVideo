//! IMV_DEBUG 测试包的启动边界：展开随包运行时、等就绪回执，退出时关闭监督管道。

use std::{
    fs,
    io::{BufRead, BufReader, Write},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::Manager;

/// 缓存一次启动结果，React StrictMode 或重复调用不会启动第二套数据库。
#[derive(Default)]
pub struct Backend {
    result: Mutex<Option<Result<String, String>>>,
    child: Mutex<Option<Child>>,
    stopping: AtomicBool,
}

/// 开发/普通安装包不启动后端；仅构建时 IMV_DEBUG=true 的测试包使用内置服务。
#[tauri::command]
pub async fn start_backend(app: tauri::AppHandle) -> Result<Option<String>, String> {
    if option_env!("IMV_DEBUG") != Some("true") || tauri::is_dev() {
        return Ok(None);
    }
    return tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<Backend>();
        let mut cached = state.result.lock().map_err(|error| error.to_string())?;
        if let Some(result) = &*cached {
            return result.clone().map(Some);
        }
        let result = launch(&app, &state);
        *cached = Some(result.clone());
        result.map(Some)
    })
    .await
    .map_err(|error| error.to_string())?;
}

/// 按归档摘要缓存运行时，原子发布解包目录；日志与数据库独立保存在应用数据目录。
fn launch(app: &tauri::AppHandle, state: &Backend) -> Result<String, String> {
    let run = || -> Result<(Child, std::path::PathBuf), Box<dyn std::error::Error>> {
        // 解包也可能失败；提前建立日志，让客户和 CI 能看到 Python 启动前的诊断。
        let data = app.path().app_data_dir()?.join("backend");
        fs::create_dir_all(&data)?;
        let mut log = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(data.join("server.log"))?;
        let resources = app.path().resource_dir()?;
        writeln!(
            log,
            "Preparing bundled backend from {}",
            resources.display()
        )?;
        let id = fs::read_to_string(resources.join("backend.id"))?;
        let id = id.trim();
        if id.len() != 64 || !id.bytes().all(|value| value.is_ascii_hexdigit()) {
            return Err("内置运行时标识无效".into());
        }
        let cache = app.path().app_cache_dir()?.join("backend");
        fs::create_dir_all(&cache)?;
        let runtime = cache.join(id);
        if !runtime.exists() {
            let staging = cache.join(uuid::Uuid::new_v4().to_string());
            fs::create_dir(&staging)?;
            writeln!(log, "Extracting backend to {}", staging.display())?;
            let extracted = command("tar")
                .arg("-xf")
                .arg(resources.join("backend.tar"))
                .arg("-C")
                .arg(&staging)
                .stdout(log.try_clone()?)
                .stderr(log.try_clone()?)
                .status();
            if !extracted?.success() {
                let _ = fs::remove_dir_all(&staging);
                return Err("内置运行时解包失败，请检查磁盘空间".into());
            }
            if let Err(error) = fs::rename(&staging, &runtime) {
                let _ = fs::remove_dir_all(&staging);
                if !runtime.exists() {
                    return Err(error.into());
                }
            }
        }
        writeln!(log, "Starting bundled Python from {}", runtime.display())?;
        let mut process = command(runtime.join(if cfg!(windows) {
            "python/python.exe"
        } else {
            "python/bin/python3"
        }));
        process
            .args(["-I", "-X", "utf8", "-m", "server.desktop"])
            .arg(&runtime)
            .arg(&data)
            // 不继承 AppImage 给前端设置的库搜索路径。
            .env_remove("LD_LIBRARY_PATH")
            .env_remove("LD_PRELOAD")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(log);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            process.process_group(0);
        }
        let process = process.spawn()?;
        Ok((process, data))
    };
    let (process, data) = run().map_err(|error| format!("内置服务启动失败：{error}"))?;
    finish_start(process, &data, state)
}

/// 接收就绪回执；任何读取或校验失败都关闭管道并回收监督进程。
fn finish_start(
    mut process: Child,
    data: &std::path::Path,
    state: &Backend,
) -> Result<String, String> {
    let stdout = process.stdout.take();
    {
        let mut child = state
            .child
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        *child = Some(process);
    }
    let result = (|| {
        if state.stopping.load(Ordering::SeqCst) {
            return Err("客户端已退出".into());
        }
        let stdout = stdout.ok_or("缺少服务就绪管道")?;
        let mut line = String::new();
        BufReader::new(stdout)
            .read_line(&mut line)
            .map_err(|error| error.to_string())?;
        let response: serde_json::Value = serde_json::from_str(&line).map_err(|_| {
            format!(
                "内置服务启动失败，请查看 {}",
                data.join("server.log").display()
            )
        })?;
        let url = response["url"]
            .as_str()
            .and_then(|value| tauri::Url::parse(value).ok());
        url.filter(|url| {
            url.scheme() == "http"
                && url.host_str() == Some("127.0.0.1")
                && url.port().is_some_and(|port| port > 0)
                && url.username().is_empty()
                && url.password().is_none()
                && url.path() == "/"
                && url.query().is_none()
                && url.fragment().is_none()
        })
        .map(|url| url.as_str().trim_end_matches('/').to_owned())
        .ok_or_else(|| "内置 API 没有返回回环地址".to_owned())
    })();
    if result.is_err() {
        stop_child(state);
    }
    result
}

/// 给 API/MySQL 正常关闭留出有界时间，随后回收仍未退出的监督进程。
fn stop_child(state: &Backend) {
    let child = state
        .child
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .take();
    if let Some(mut child) = child {
        drop(child.stdin.take());
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
        while matches!(child.try_wait(), Ok(None)) && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        if !matches!(child.try_wait(), Ok(Some(_))) {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// 关闭 stdin 让 Python 先停止 API 再关闭 MySQL；不删除持久化数据。
pub fn shutdown(app: &tauri::AppHandle) {
    {
        let state = app.state::<Backend>();
        state.stopping.store(true, Ordering::SeqCst);
        if let Ok(mut child) = state.child.lock() {
            if let Some(child) = child.as_mut() {
                drop(child.stdin.take());
            }
        };
    }
}

/// Windows 后端工具不创建额外控制台窗口；所有平台保留相同的参数与管道协议。
fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    #[cfg(not(windows))]
    let _ = &mut command;
    command
}

#[cfg(all(test, unix))]
mod tests {
    /// 坏 JSON、伪装地址和 EOF 会回收进程；合法回执则让服务持续运行。
    #[test]
    fn readiness_controls_child_lifetime() {
        let dir = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir(&dir).unwrap();
        for (reply, success) in [
            ("invalid", false),
            ("EOF", false),
            (r#"{"url":"http://127.0.0.1:1234@evil.test"}"#, false),
            (r#"{"url":"http://127.0.0.1:0"}"#, false),
            (r#"{"url":"http://127.0.0.1:23456"}"#, true),
        ] {
            let marker = dir.join("stopped");
            let child = super::command("sh")
                .args(["-c", "if [ \"$1\" = EOF ]; then exec 1>&-; else printf '%s\\n' \"$1\"; fi; cat >/dev/null; printf done >\"$2\"", "test", reply])
                .arg(&marker).stdin(super::Stdio::piped()).stdout(super::Stdio::piped()).spawn().unwrap();
            let state = super::Backend::default();
            let result = super::finish_start(child, &dir, &state);
            let reaped = state.child.lock().unwrap().is_none() && marker.exists();
            // 即使用例失败也先清理进程，避免测试泄漏。
            super::stop_child(&state);
            assert_eq!(result.is_ok(), success);
            assert_eq!(reaped, !success);
            assert_eq!(std::fs::read_to_string(&marker).unwrap(), "done");
            std::fs::remove_file(marker).unwrap();
        }
        std::fs::remove_dir(dir).unwrap();
    }
}
