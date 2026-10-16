//! 模板编辑的本地预览视频：用户为视频 1～3 选择的文件只复制到应用数据目录
//! `data/preview_videos/`，不上传服务端。每个槽位的元数据 `video-N.json`（文件名、MIME、字节数与
//! 数据文件名）是唯一提交点，指向唯一命名的数据文件 `video-N-<uuid>`；前端启动后读回字节生成 blob URL 播放。

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use tauri::Manager;
use uuid::Uuid;

/// 单个视频上限；字节需完整经过 IPC 并在网页内存中生成 blob，过大的文件不适合预览。
const MAX_BYTES: usize = 512 * 1024 * 1024;

/// 槽位元数据；`file` 必须是本槽位的数据文件名且 `size` 与其长度一致才视为有效，
/// 替换中断留下的新旧数据不会被误读，旧元数据始终指向完整的旧文件。
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Stored {
    name: String,
    #[serde(rename = "type")]
    kind: String,
    size: u64,
    #[serde(default)]
    file: String,
}

/// 视频编号限定为 1～3，返回该槽位名 `video-N`。
fn slot_name(slot: u8) -> Result<String, String> {
    if (1..=3).contains(&slot) {
        Ok(format!("video-{slot}"))
    } else {
        Err("预览视频编号须为 1～3".into())
    }
}

/// 数据文件名是否属于该槽位：`video-N-` 加字母数字，拒绝路径分隔符等被篡改的元数据。
fn owns(name: &str, file: &str) -> bool {
    file.strip_prefix(&format!("{name}-")).is_some_and(|rest| {
        !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_alphanumeric())
    })
}

/// 校验视频字节数：空文件与超限文件均拒绝，写入前执行。
fn check_len(len: usize) -> Result<(), String> {
    if len == 0 {
        return Err("本地视频文件为空".into());
    }
    if len > MAX_BYTES {
        return Err("本地视频不能超过 512 MiB".into());
    }
    Ok(())
}

/// 在目录锁下执行操作：写入与删除独占，竞争时报错供用户重试；列表与读取共享，
/// 互相不阻塞，只等待进行中的写入完成，因此看不到中间状态。
fn locked<T>(
    directory: &Path,
    exclusive: bool,
    run: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    fs::create_dir_all(directory).map_err(|error| format!("无法创建本地视频目录：{error}"))?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(".lock"))
        .map_err(|error| format!("无法打开本地视频锁：{error}"))?;
    if exclusive {
        lock.try_lock()
            .map_err(|_| "本地预览视频正在被其他操作使用，请重试")?;
    } else {
        lock.lock_shared()
            .map_err(|error| format!("无法获取本地视频锁：{error}"))?;
    }
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

/// 读取一个槽位的有效元数据；缺失、损坏、数据文件名不属于本槽位或长度不一致时返回 None。
fn stored(directory: &Path, slot: u8) -> Result<Option<Stored>, String> {
    let name = slot_name(slot)?;
    let Ok(bytes) = fs::read(directory.join(format!("{name}.json"))) else {
        return Ok(None);
    };
    let Ok(meta) = serde_json::from_slice::<Stored>(&bytes) else {
        return Ok(None);
    };
    if !owns(&name, &meta.file) {
        return Ok(None);
    }
    let size = fs::metadata(directory.join(&meta.file)).map(|data| data.len());
    Ok((size.ok() == Some(meta.size)).then_some(meta))
}

/// 删除本槽位除 `keep` 外的数据文件与中断遗留的临时文件；尽力而为，失败的残留不被元数据引用，下次写入或删除再清理。
fn sweep(directory: &Path, name: &str, keep: Option<&str>) {
    let prefix = format!("{name}-");
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        let file = entry.file_name();
        let file = file.to_string_lossy();
        if file.starts_with(&prefix) && keep != Some(&*file) {
            let _ = fs::remove_file(entry.path());
        }
    }
}

/// 列出三个槽位，没有本地视频的为 None；共享锁保证不会读到写入或删除的中间状态。
fn list(directory: &Path) -> Result<Vec<Option<Stored>>, String> {
    locked(directory, false, || {
        (1..=3).map(|slot| stored(directory, slot)).collect()
    })
}

/// 读取槽位的视频字节；没有有效本地视频时报错。多个槽位可同时读取。
fn read(directory: &Path, slot: u8) -> Result<Vec<u8>, String> {
    locked(directory, false, || {
        let meta = stored(directory, slot)?.ok_or(format!("视频 {slot} 没有本地文件"))?;
        fs::read(directory.join(meta.file))
            .map_err(|error| format!("读取视频 {slot} 失败：{error}"))
    })
}

/// 校验后把新数据写入唯一命名的文件，再替换元数据作为提交点，最后清理旧数据；
/// 提交前任一步失败或中断，旧元数据仍指向完整的旧视频。空文件、超限文件与非法编号不写入。
fn write(directory: &Path, slot: u8, name: &str, kind: &str, bytes: &[u8]) -> Result<(), String> {
    let slot_name = slot_name(slot)?;
    check_len(bytes.len())?;
    let meta = Stored {
        name: name.to_owned(),
        kind: kind.to_owned(),
        size: bytes.len() as u64,
        file: format!("{slot_name}-{}", Uuid::new_v4().simple()),
    };
    let json = serde_json::to_vec(&meta).map_err(|error| error.to_string())?;
    locked(directory, true, || {
        replace(&directory.join(&meta.file), bytes)?;
        if let Err(error) = replace(&directory.join(format!("{slot_name}.json")), &json) {
            let _ = fs::remove_file(directory.join(&meta.file));
            return Err(error);
        }
        sweep(directory, &slot_name, Some(&meta.file));
        Ok(())
    })
}

/// 删除槽位的元数据（提交点）并清理数据；不存在视为已删除，数据清理失败不报错，残留文件已不被引用。
fn remove(directory: &Path, slot: u8) -> Result<(), String> {
    let name = slot_name(slot)?;
    locked(directory, true, || {
        match fs::remove_file(directory.join(format!("{name}.json"))) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => {
                return Err(format!("删除本地视频失败：{error}"));
            }
            _ => {}
        }
        sweep(directory, &name, None);
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
/// 请求体由 Tauri 持有，借用写入避免再复制一份视频字节；落盘与刷盘经 `block_in_place` 让出异步工作线程。
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
    let directory = directory(&app)?;
    tokio::task::block_in_place(|| write(&directory, slot, &name, header("x-imv-type"), bytes))
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

    /// 数据目录中属于该槽位的文件名（不含元数据），用于检查替换与删除后没有残留。
    fn data_files(dir: &Path, slot: u8) -> Vec<String> {
        let prefix = format!("video-{slot}-");
        fs::read_dir(dir)
            .unwrap()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .filter(|file| file.starts_with(&prefix))
            .collect()
    }

    /// 写入后列出元数据并原样读回；替换只影响同一槽位且不留旧数据，删除后回到空槽位且可重复删除。
    #[test]
    fn write_read_replace_and_remove() {
        let dir = Directory::new();
        assert_eq!(list(&dir.0).unwrap(), vec![None, None, None]);
        write(&dir.0, 2, "片段.mp4", "video/mp4", b"first").unwrap();
        write(&dir.0, 3, "c.webm", "video/webm", b"third").unwrap();
        write(&dir.0, 2, "替换.mov", "video/quicktime", b"second!").unwrap();
        let stored = list(&dir.0).unwrap();
        assert_eq!(stored[0], None);
        let second = stored[1].clone().unwrap();
        assert_eq!(
            (second.name.as_str(), second.kind.as_str(), second.size),
            ("替换.mov", "video/quicktime", 7)
        );
        assert_eq!(data_files(&dir.0, 2), vec![second.file]);
        assert_eq!(read(&dir.0, 2).unwrap(), b"second!");
        assert_eq!(read(&dir.0, 3).unwrap(), b"third");
        remove(&dir.0, 2).unwrap();
        remove(&dir.0, 2).unwrap();
        assert_eq!(list(&dir.0).unwrap()[1], None);
        assert!(data_files(&dir.0, 2).is_empty());
        assert!(read(&dir.0, 2).unwrap_err().contains("没有本地文件"));
        assert_eq!(read(&dir.0, 3).unwrap(), b"third");
    }

    /// 非法编号、空文件和超限长度均不写入，已有视频保留；超限只校验长度，不分配 512 MiB 缓冲。
    #[test]
    fn rejects_invalid_input_without_writes() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"keep").unwrap();
        assert!(write(&dir.0, 0, "a.mp4", "video/mp4", b"x").is_err());
        assert!(write(&dir.0, 4, "a.mp4", "video/mp4", b"x").is_err());
        assert!(write(&dir.0, 1, "a.mp4", "video/mp4", b"").is_err());
        assert!(check_len(MAX_BYTES).is_ok());
        assert!(check_len(MAX_BYTES + 1).unwrap_err().contains("512 MiB"));
        assert!(remove(&dir.0, 4).is_err());
        assert_eq!(read(&dir.0, 1).unwrap(), b"keep");
        assert_eq!(data_files(&dir.0, 1).len(), 1);
    }

    /// 元数据损坏、数据长度不一致或数据文件名被篡改（含路径穿越）时视为没有本地视频。
    #[test]
    fn ignores_inconsistent_metadata() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"abc").unwrap();
        let meta = list(&dir.0).unwrap()[0].clone().unwrap();
        fs::write(dir.0.join(&meta.file), b"abcdef").unwrap();
        assert_eq!(list(&dir.0).unwrap()[0], None);
        assert!(read(&dir.0, 1).is_err());
        for file in ["../escape", "video-2-abc", "video-1-", "video-1-a/b"] {
            fs::write(dir.0.join("escape"), b"abc").unwrap();
            let tampered = Stored {
                file: file.into(),
                size: 3,
                ..meta.clone()
            };
            fs::write(
                dir.0.join("video-1.json"),
                serde_json::to_vec(&tampered).unwrap(),
            )
            .unwrap();
            assert_eq!(list(&dir.0).unwrap()[0], None, "{file}");
        }
        fs::write(dir.0.join("video-1.json"), b"broken").unwrap();
        assert_eq!(list(&dir.0).unwrap()[0], None);
    }

    /// 元数据替换前中断只留下未被引用的新数据：旧视频仍可读，下次写入清理残留；
    /// 删除只需元数据提交，残留数据不再被引用并在下次删除时清理。
    #[test]
    fn interrupted_replace_keeps_previous_video() {
        let dir = Directory::new();
        write(&dir.0, 1, "a.mp4", "video/mp4", b"old").unwrap();
        fs::write(dir.0.join("video-1-deadbeef"), b"half written new").unwrap();
        fs::write(dir.0.join("video-1-deadbeef.1234.tmp"), b"tmp").unwrap();
        assert_eq!(read(&dir.0, 1).unwrap(), b"old");
        assert_eq!(list(&dir.0).unwrap()[0].as_ref().unwrap().name, "a.mp4");
        write(&dir.0, 1, "b.mp4", "video/mp4", b"new").unwrap();
        assert_eq!(read(&dir.0, 1).unwrap(), b"new");
        assert_eq!(data_files(&dir.0, 1).len(), 1);
        fs::write(dir.0.join("video-1-leftover"), b"orphan").unwrap();
        remove(&dir.0, 1).unwrap();
        assert!(data_files(&dir.0, 1).is_empty());
    }

    /// 多个槽位同时读取互不冲突，也不会因共享锁报错。
    #[test]
    fn concurrent_reads_share_the_lock() {
        let dir = Directory::new();
        for slot in 1..=3u8 {
            write(&dir.0, slot, "a.mp4", "video/mp4", &[slot; 4]).unwrap();
        }
        let path = &dir.0;
        std::thread::scope(|scope| {
            let reads: Vec<_> = (1..=3u8)
                .map(|slot| scope.spawn(move || (read(path, slot).unwrap(), list(path).unwrap())))
                .collect();
            for (index, handle) in reads.into_iter().enumerate() {
                let (bytes, stored) = handle.join().unwrap();
                assert_eq!(bytes, vec![index as u8 + 1; 4]);
                assert!(stored.iter().all(Option::is_some));
            }
        });
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
        assert_eq!(data_files(&dir.0, 1).len(), 1);
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
