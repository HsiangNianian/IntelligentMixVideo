//! 尚未写入会话库的公开任务回执；固定应用目录和文件锁跨窗口来源、进程及数据库版本保留恢复入口。

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs, io::Write, path::Path};

/// 服务和任务共同确定一条回执；任务沿用服务端公开 JSON，不接收凭据。
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Registration {
    server: String,
    job: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    initial: Option<Value>,
}

impl Registration {
    /// 损坏或不完整的回执不得静默放行更新，也不得覆盖原文件。
    fn validate(&self) -> Result<(), String> {
        if self.server.is_empty()
            || ["id", "project_id"].iter().any(|key| {
                !self.job[key]
                    .as_str()
                    .is_some_and(|value| !value.is_empty())
            })
        {
            return Err("任务登记恢复记录无效，请先修复本地存储".into());
        }
        if let Some(initial) = &self.initial {
            if initial["work"]["work"]["id"] != self.job["project_id"]
                || initial["work"]["job"]["id"] != self.job["id"]
            {
                return Err("初始会话与任务回执不匹配".into());
            }
        }
        Ok(())
    }
}

/// 删除只接受服务与任务 ID，避免清除其他服务的同名任务。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Remove {
    server: String,
    id: String,
}

/// 在独立文件锁内读改写；不打开 SQLite，旧壳仍能核实回执并完成整包升级。
pub(crate) fn operate(directory: &Path, operation: &str, payload: Value) -> Result<Value, String> {
    fs::create_dir_all(directory).map_err(|_| "创建任务回执目录失败")?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(".registrations.lock"))
        .map_err(|_| "打开任务回执锁失败")?;
    crate::file_lock::lock_exclusive(&lock).map_err(|_| "任务回执正在被其他操作使用，请重试")?;
    let path = directory.join("registrations.json");
    let mut entries: Vec<Registration> = match fs::read(&path) {
        Ok(bytes) => {
            serde_json::from_slice(&bytes).map_err(|_| "任务登记恢复记录无效，请先修复本地存储")?
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(_) => return Err("读取任务登记恢复记录失败".into()),
    };
    for entry in &entries {
        entry.validate()?;
    }
    match operation {
        "registrations_list" if payload == json!({}) => return Ok(json!(entries)),
        "registrations_save" => {
            let entry: Registration =
                serde_json::from_value(payload).map_err(|_| "任务登记回执参数无效")?;
            entry.validate()?;
            entries.retain(|old| old.server != entry.server || old.job["id"] != entry.job["id"]);
            entries.push(entry);
        }
        "registrations_remove" => {
            let query: Remove =
                serde_json::from_value(payload).map_err(|_| "任务登记回执参数无效")?;
            entries.retain(|entry| entry.server != query.server || entry.job["id"] != query.id);
        }
        _ => return Err("未知或无效的任务回执操作".into()),
    }
    let temporary = directory.join("registrations.json.tmp");
    let write = || -> std::io::Result<()> {
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&serde_json::to_vec(&entries)?)?;
        file.sync_all()?;
        fs::rename(&temporary, &path)
    };
    if write().is_err() {
        let _ = fs::remove_file(temporary);
        return Err("保存任务登记恢复记录失败".into());
    }
    Ok(Value::Null)
}
