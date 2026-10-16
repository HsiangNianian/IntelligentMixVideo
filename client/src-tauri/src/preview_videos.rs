//! 模板编辑的本地预览视频：用户为视频 1～3 选择的文件只复制到应用数据目录
//! `data/preview_videos/`，不上传服务端。每个槽位保存 `video-N`（原始字节）与
//! `video-N.json`（文件名、MIME 与字节数）；前端启动后读回字节生成 blob URL 播放。

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use tauri::Manager;
use uuid::Uuid;

/// 单个视频上限；字节需完整经过 IPC 并在网页内存中生成 blob，过大的文件不适合预览。
const MAX_BYTES: usize = 512 * 1024 * 1024;

/// 槽位元数据；`size` 与数据文件长度一致才视为有效，替换中断留下的旧数据不会被误读。
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Stored {
    name: String,
    #[serde(rename = "type")]
    kind: String,
    size: u64,
}

/// 视频编号限定为 1～3，返回该槽位数据文件名。
fn slot_file(slot: u8) -> Result<String, String> {
    if (1..=3).contains(&slot) {
        Ok(format!("video-{slot}"))
    } else {
        Err("预览视频编号须为 1～3".into())
    }
}

/// 在目录锁下执行操作，与其他窗口或实例的并发写入互斥；竞争时报错供用户重试。
fn locked<T>(directory: &Path, run: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    fs::create_dir_all(directory).map_err(|error| format!("无法创建本地视频目录：{error}"))?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(".lock"))
        .map_err(|error| format!("无法打开本地视频锁：{error}"))?;
    lock.try_lock()
        .map_err(|_| "本地预览视频正在被其他操作使用，请重试")?;
    run()
}

/// 同目录唯一临时文件刷盘后替换目标；失败只删除本次临时文件，原文件保留。
fn replace(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let temporary = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("保存本地视频失败：{error}"))?;
    let written = file.write_all(bytes).and_then(|_| file.sync_all());
    drop(file);
    if let Err(error) = written.and_then(|_| fs::rename(&temporary, path)) {
        let _ = fs::remove_file(&temporary);
        return Err(format!("保存本地视频失败：{error}"));
    }
    Ok(())
}

/// 读取一个槽位的有效元数据；缺失、损坏或与数据文件长度不一致时返回 None。
fn stored(directory: &Path, slot: u8) -> Result<Option<Stored>, String> {
    let file = slot_file(slot)?;
    let Ok(bytes) = fs::read(directory.join(format!("{file}.json"))) else {
        return Ok(None);
    };
    let Ok(meta) = serde_json::from_slice::<Stored>(&bytes) else {
        return Ok(None);
    };
    let size = fs::metadata(directory.join(&file)).map(|data| data.len());
    Ok((size.ok() == Some(meta.size)).then_some(meta))
}

/// 列出三个槽位，没有本地视频的为 None。
fn list(directory: &Path) -> Result<Vec<Option<Stored>>, String> {
    (1..=3).map(|slot| stored(directory, slot)).collect()
}

/// 读取槽位的视频字节；没有有效本地视频时报错。
fn read(directory: &Path, slot: u8) -> Result<Vec<u8>, String> {
    locked(directory, || {
        stored(directory, slot)?.ok_or(format!("视频 {slot} 没有本地文件"))?;
        fs::read(directory.join(slot_file(slot)?))
            .map_err(|error| format!("读取视频 {slot} 失败：{error}"))
    })
}

/// 校验后先替换数据再替换元数据；空文件、超限文件与非法编号不写入。
fn write(directory: &Path, slot: u8, name: &str, kind: &str, bytes: &[u8]) -> Result<(), String> {
    let file = slot_file(slot)?;
    if bytes.is_empty() {
        return Err("本地视频文件为空".into());
    }
    if bytes.len() > MAX_BYTES {
        return Err("本地视频不能超过 512 MiB".into());
    }
    let meta = Stored {
        name: name.to_owned(),
        kind: kind.to_owned(),
        size: bytes.len() as u64,
    };
    let json = serde_json::to_vec(&meta).map_err(|error| error.to_string())?;
    locked(directory, || {
        replace(&directory.join(&file), bytes)?;
        replace(&directory.join(format!("{file}.json")), &json)
    })
}

/// 删除槽位的元数据与数据；不存在视为已删除。
fn remove(directory: &Path, slot: u8) -> Result<(), String> {
    let file = slot_file(slot)?;
    locked(directory, || {
        for path in [
            directory.join(format!("{file}.json")),
            directory.join(&file),
        ] {
            match fs::remove_file(path) {
                Err(error) if error.kind() != std::io::ErrorKind::NotFound => {
                    return Err(format!("删除本地视频失败：{error}"))
                }
                _ => {}
            }
        }
        Ok(())
    })
}

/// 应用数据目录下的固定存储位置，不接受前端指定路径。
fn directory(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|_| "读取应用目录失败")?
        .join("data/preview_videos"))
}

/// 列出已保存的本地预览视频元数据。
#[tauri::command]
pub async fn preview_video_list(app: tauri::AppHandle) -> Result<Vec<Option<Stored>>, String> {
    let directory = directory(&app)?;
    tauri::async_runtime::spawn_blocking(move || list(&directory))
        .await
        .map_err(|_| "本地视频读取失败".to_string())?
}

/// 以二进制响应返回视频字节，前端收到 ArrayBuffer。
#[tauri::command]
pub async fn preview_video_read(
    app: tauri::AppHandle,
    slot: u8,
) -> Result<tauri::ipc::Response, String> {
    let directory = directory(&app)?;
    tauri::async_runtime::spawn_blocking(move || read(&directory, slot))
        .await
        .map_err(|_| "本地视频读取失败".to_string())?
        .map(tauri::ipc::Response::new)
}

/// 接收原始二进制请求体；`x-imv-slot`、URI 编码的 `x-imv-name` 与 `x-imv-type` 头描述该文件。
/// 请求体由 Tauri 持有，直接在异步线程写入，避免再复制一份视频字节。
#[tauri::command]
pub async fn preview_video_write(
    app: tauri::AppHandle,
    request: tauri::ipc::Request<'_>,
) -> Result<(), String> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else {
        return Err("本地视频须以二进制上传".into());
    };
    let header = |name: &str| {
        request
            .headers()
            .get(name)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
    };
    let slot = header("x-imv-slot")
        .parse()
        .map_err(|_| "预览视频编号须为 1～3")?;
    let name = percent_decode(header("x-imv-name"));
    write(&directory(&app)?, slot, &name, header("x-imv-type"), bytes)
}

/// 删除一个槽位的本地视频，之后该槽位回到默认地址。
#[tauri::command]
pub async fn preview_video_remove(app: tauri::AppHandle, slot: u8) -> Result<(), String> {
    let directory = directory(&app)?;
    tauri::async_runtime::spawn_blocking(move || remove(&directory, slot))
        .await
        .map_err(|_| "本地视频删除失败".to_string())?
}

/// 解码前端 `encodeURIComponent` 的文件名；非法序列按原文保留，文件名只用于展示。
fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let hex = bytes
            .get(index + 1..index + 3)
            .and_then(|pair| std::str::from_utf8(pair).ok())
            .and_then(|pair| u8::from_str_radix(pair, 16).ok());
        match (bytes[index], hex) {
            (b'%', Some(byte)) => {
                decoded.push(byte);
                index += 3;
            }
            (byte, _) => {
                decoded.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8(decoded).unwrap_or_else(|_| value.to_owned())
}

#[cfg(test)]
mod tests {
    //! 临时目录验证写入往返、替换与删除、拒绝不写入、元数据不一致与锁冲突；
    //! 执行 cargo test --locked --lib preview_videos。
    use super::*;

    /// 每例独立目录，退出时清理，不接触真实应用数据。
    struct Directory(PathBuf);
    impl Directory {
        /// 以随机目录隔离并行测试。
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("imv-preview-video-test-{}", Uuid::new_v4())))
        }
    }
    impl Drop for Directory {
        /// 即使断言失败也清理该用例的临时数据。
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// 写入后列出元数据并原样读回；替换只影响同一槽位，删除后回到空槽位且可重复删除。
    #[test]
    fn write_read_replace_and_remove() {
        let dir = Directory::new();
        assert_eq!(list(&dir.0).unwrap(), vec![None, None, None]);
        write(&dir.0, 2, "片段.mp4", "video/mp4", b"first").unwrap();
        write(&dir.0, 3, "c.webm", "video/webm", b"third").unwrap();
        write(&dir.0, 2, "替换.mov", "video/quicktime", b"second!").unwrap();
        let stored = list(&dir.0).unwrap();
        assert_eq!(stored[0], None);
        assert_eq!(
            stored[1],
            Some(Stored {
                name: "替换.mov".into(),
                kind: "video/quicktime".into(),
                size: 7
            })
        );
        assert_eq!(read(&dir.0, 2).unwrap(), b"second!");
        assert_eq!(read(&dir.0, 3).unwrap(), b"third");
        remove(&dir.0, 2).unwrap();
        remove(&dir.0, 2).unwrap();
        assert_eq!(list(&dir.0).unwrap()[1], None);
        assert!(read(&dir.0, 2).unwrap_err().contains("没有本地文件"));
        assert_eq!(read(&dir.0, 3).unwrap(), b"third");
    }

    /// 非法编号、空文件和超限文件均不写入，已有视频保留。
    #[test]
    fn rejects_invalid_input_without_writes() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"keep").unwrap();
        assert!(write(&dir.0, 0, "a.mp4", "video/mp4", b"x").is_err());
        assert!(write(&dir.0, 4, "a.mp4", "video/mp4", b"x").is_err());
        assert!(write(&dir.0, 1, "a.mp4", "video/mp4", b"").is_err());
        let large = vec![0; MAX_BYTES + 1];
        assert!(write(&dir.0, 1, "a.mp4", "video/mp4", &large)
            .unwrap_err()
            .contains("512 MiB"));
        assert!(remove(&dir.0, 4).is_err());
        assert_eq!(read(&dir.0, 1).unwrap(), b"keep");
    }

    /// 元数据损坏或与数据长度不一致（替换中断）时视为没有本地视频。
    #[test]
    fn ignores_inconsistent_metadata() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"abc").unwrap();
        fs::write(dir.0.join("video-1"), b"abcdef").unwrap();
        assert_eq!(list(&dir.0).unwrap()[0], None);
        assert!(read(&dir.0, 1).is_err());
        fs::write(dir.0.join("video-1.json"), b"broken").unwrap();
        assert_eq!(list(&dir.0).unwrap()[0], None);
    }

    /// 目录锁被占用时写入、删除报错并保留原文件，释放后可以重试。
    #[test]
    fn lock_conflict_preserves_files() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"keep").unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(dir.0.join(".lock"))
            .unwrap();
        lock.try_lock().unwrap();
        assert!(write(&dir.0, 1, "b.mp4", "video/mp4", b"new")
            .unwrap_err()
            .contains("请重试"));
        assert!(remove(&dir.0, 1).is_err());
        drop(lock);
        assert_eq!(read(&dir.0, 1).unwrap(), b"keep");
        write(&dir.0, 1, "b.mp4", "video/mp4", b"new").unwrap();
        assert_eq!(read(&dir.0, 1).unwrap(), b"new");
    }

    /// 文件名按 URI 编码传输，解码中文与保留字符；非法序列原样保留。
    #[test]
    fn decodes_file_names() {
        assert_eq!(
            percent_decode("%E7%89%87%E6%AE%B5%20(1).mp4"),
            "片段 (1).mp4"
        );
        assert_eq!(percent_decode("bad%zz%"), "bad%zz%");
    }
}
