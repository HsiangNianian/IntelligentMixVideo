//! 客户端插件配置存入固定应用目录的 JSON；文件锁保护完整读改写，临时文件替换保留旧数据。

use serde_json::{json, Value};
use std::{fs, io::Write, path::Path};
use tauri::Manager;

/// 文件锁覆盖读取到替换；竞争时立即报错供用户重试，ID 仅作为 JSON 键。
fn operate(directory: &Path, id: Option<&str>, values: Option<Value>) -> Result<Value, String> {
    fs::create_dir_all(directory).map_err(|_| "创建本地设置目录失败")?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(".lock"))
        .map_err(|_| "打开本地设置锁失败")?;
    lock.try_lock()
        .map_err(|_| "本地设置正在被其他操作使用，请重试")?;
    let path = directory.join("settings.json");
    let mut settings = match fs::read(&path) {
        Ok(bytes) => {
            serde_json::from_slice::<Value>(&bytes).map_err(|_| "本地设置文件损坏".to_string())?
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => json!({}),
        Err(_) => return Err("读取本地设置失败".into()),
    };
    let entries = settings.as_object_mut().ok_or("本地设置格式错误")?;
    if let Some(id) = id {
        let values = values
            .filter(Value::is_object)
            .ok_or("插件配置必须为对象")?;
        entries.insert(id.to_owned(), values);
        let temporary = directory.join("settings.json.tmp");
        let write = || -> std::io::Result<()> {
            let mut options = fs::OpenOptions::new();
            options.write(true).create(true).truncate(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options.open(&temporary)?;
            file.write_all(&serde_json::to_vec_pretty(&settings)?)?;
            file.sync_all()?;
            fs::rename(&temporary, &path)
        };
        if write().is_err() {
            let _ = fs::remove_file(temporary);
            return Err("保存本地设置失败".into());
        }
    }
    Ok(settings)
}

/// 省略 ID 读取全部设置，携带 ID/values 保存单个插件；不访问后端或任意用户路径。
#[tauri::command]
pub fn local_settings(
    app: tauri::AppHandle,
    id: Option<String>,
    values: Option<Value>,
) -> Result<Value, String> {
    // ponytail: 密钥与普通配置同存本地 JSON；需要加密存储时再接系统凭据库。
    let directory = app
        .path()
        .app_data_dir()
        .map_err(|_| "读取应用目录失败")?
        .join("data/settings");
    operate(&directory, id.as_deref(), values)
}

#[cfg(test)]
mod tests {
    //! 临时目录验证持久化与插件隔离；执行 cargo test --locked --lib settings。
    use super::*;

    /// 测试目录退出时清理，不读取真实客户端配置。
    struct Directory(std::path::PathBuf);
    impl Drop for Directory {
        /// 清除本例产生的临时文件。
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// 多次独立读取恢复配置，更新一个插件不删除另一个；损坏文件不被覆盖。
    #[test]
    fn persists_plugins_without_overwriting_others() {
        let directory =
            Directory(std::env::temp_dir().join(format!("imv-settings-{}", uuid::Uuid::new_v4())));
        assert_eq!(operate(&directory.0, None, None).unwrap(), json!({}));
        operate(
            &directory.0,
            Some("segmentation"),
            Some(json!({"llm_model": "first", "llm_api_key": "local-key"})),
        )
        .unwrap();
        operate(
            &directory.0,
            Some("asr"),
            Some(json!({"dashscope_api_key": "asr-key"})),
        )
        .unwrap();
        operate(
            &directory.0,
            Some("segmentation"),
            Some(json!({"llm_model": "second"})),
        )
        .unwrap();
        let saved = operate(&directory.0, None, None).unwrap();
        assert_eq!(saved["segmentation"]["llm_model"], "second");
        assert_eq!(saved["asr"]["dashscope_api_key"], "asr-key");
        let path = directory.0.join("settings.json");
        let original = fs::read(&path).unwrap();
        assert!(operate(&directory.0, Some("asr"), Some(json!(null))).is_err());
        assert_eq!(fs::read(&path).unwrap(), original);
        fs::write(&path, "broken").unwrap();
        assert!(operate(&directory.0, Some("asr"), Some(json!({}))).is_err());
        assert_eq!(fs::read_to_string(path).unwrap(), "broken");
    }

    /// 持锁时读写都明确失败且原文件不变；释放锁后可保存，避免依赖线程调度复现竞争。
    #[test]
    fn lock_contention_preserves_file_and_allows_retry() {
        let directory =
            Directory(std::env::temp_dir().join(format!("imv-settings-{}", uuid::Uuid::new_v4())));
        operate(&directory.0, Some("existing"), Some(json!({"value": 1}))).unwrap();
        let path = directory.0.join("settings.json");
        let original = fs::read(&path).unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(directory.0.join(".lock"))
            .unwrap();
        lock.try_lock().unwrap();
        for id in [None, Some("asr")] {
            assert!(operate(&directory.0, id, Some(json!({})))
                .unwrap_err()
                .contains("其他操作"));
        }
        assert_eq!(fs::read(&path).unwrap(), original);
        assert!(!directory.0.join("settings.json.tmp").exists());
        drop(lock);
        operate(&directory.0, Some("asr"), Some(json!({}))).unwrap();
        assert_eq!(
            operate(&directory.0, None, None).unwrap()["existing"]["value"],
            1
        );
    }
}
