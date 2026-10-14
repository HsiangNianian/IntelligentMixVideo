//! 字效会话的本地 SQLite：聊天消息、任务公开状态、版本索引与本机设备 ID 只保存在使用者电脑。
//!
//! 库文件位于应用数据目录的 `data/remotion/sessions.sqlite3`，首次调用时创建。前端的
//! `localSessions.ts` 负责合并服务端事件，这里只按操作读写 JSON 行：`save` 在一个事务内写入会话与本批记录，
//! `remove` 删除一条会话的全部记录。并发调用由 SQLite 的忙等待超时串行化。

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use std::{path::Path, time::Duration};
use tauri::Manager;

/// 表结构只增不改；`data` 列保存前端定义的 JSON 文档。
const SCHEMA: &str = "
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS works (id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, work_id TEXT NOT NULL, sequence INTEGER NOT NULL, data TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS messages_by_work ON messages(work_id, sequence);
    CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, work_id TEXT NOT NULL, data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS jobs_by_work ON jobs(work_id);
    CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, work_id TEXT NOT NULL, data TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS versions_by_work ON versions(work_id);
";

/// 打开（必要时创建）会话库并确保表存在。
fn open(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|_| "创建本地会话目录失败")?;
    }
    let connection = Connection::open(path).map_err(|_| "打开本地会话库失败")?;
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(|_| "设置本地会话库等待时间失败")?;
    connection
        .execute_batch(&format!("PRAGMA journal_mode=WAL;{SCHEMA}"))
        .map_err(|_| "初始化本地会话库失败".to_string())?;
    Ok(connection)
}

/// 读取字符串字段；缺少或类型不符时报告字段名。
fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("本地会话请求缺少 {field}"))
}

/// 把一列 JSON 文本解析回文档；损坏的行明确报错，不当作空值。
fn documents(rows: Vec<String>) -> Result<Value, String> {
    rows.iter()
        .map(|row| serde_json::from_str(row).map_err(|_| "本地会话记录损坏".to_string()))
        .collect::<Result<Vec<Value>, _>>()
        .map(Value::Array)
}

/// 执行一次操作；所有写入都在事务内完成，失败时回滚。
fn operate(path: &Path, operation: &str, payload: &Value) -> Result<Value, String> {
    let mut connection = open(path)?;
    let failed = |_| "读写本地会话库失败".to_string();
    match operation {
        "device" => {
            let existing: Option<String> = connection
                .query_row("SELECT value FROM meta WHERE key='device_id'", [], |row| {
                    row.get(0)
                })
                .optional()
                .map_err(failed)?;
            if let Some(device) = existing {
                return Ok(json!(device));
            }
            let device = uuid::Uuid::new_v4().to_string();
            connection
                .execute(
                    "INSERT INTO meta(key, value) VALUES ('device_id', ?1)",
                    params![device],
                )
                .map_err(failed)?;
            Ok(json!(device))
        }
        "works" => {
            let mut statement = connection
                .prepare("SELECT data FROM works ORDER BY updated_at DESC, id DESC")
                .map_err(failed)?;
            let rows = statement
                .query_map([], |row| row.get(0))
                .and_then(|rows| rows.collect::<Result<Vec<String>, _>>())
                .map_err(failed)?;
            documents(rows)
        }
        "work" => {
            let data: Option<String> = connection
                .query_row(
                    "SELECT data FROM works WHERE id=?1",
                    params![text(payload, "id")?],
                    |row| row.get(0),
                )
                .optional()
                .map_err(failed)?;
            data.map_or(Ok(Value::Null), |data| {
                serde_json::from_str(&data).map_err(|_| "本地会话记录损坏".to_string())
            })
        }
        "messages" => {
            let before = payload
                .get("before")
                .and_then(Value::as_i64)
                .unwrap_or(i64::MAX);
            let limit = payload
                .get("limit")
                .and_then(Value::as_i64)
                .unwrap_or(50)
                .clamp(1, 1000);
            let mut statement = connection
                .prepare(
                    "SELECT data FROM messages WHERE work_id=?1 AND sequence<?2 ORDER BY sequence DESC LIMIT ?3",
                )
                .map_err(failed)?;
            let rows = statement
                .query_map(params![text(payload, "work")?, before, limit], |row| {
                    row.get(0)
                })
                .and_then(|rows| rows.collect::<Result<Vec<String>, _>>())
                .map_err(failed)?;
            documents(rows)
        }
        "jobs" => {
            let work = text(payload, "work")?;
            let mut rows = Vec::new();
            for id in payload
                .get("ids")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let id = id.as_str().ok_or("任务 ID 必须是字符串")?;
                let data: Option<String> = connection
                    .query_row(
                        "SELECT data FROM jobs WHERE id=?1 AND work_id=?2",
                        params![id, work],
                        |row| row.get(0),
                    )
                    .optional()
                    .map_err(failed)?;
                rows.extend(data);
            }
            documents(rows)
        }
        "save" => {
            let stored = payload.get("work").ok_or("本地会话请求缺少 work")?;
            let work = text(stored.get("work").ok_or("本地会话请求缺少作品")?, "id")?;
            let transaction = connection.transaction().map_err(failed)?;
            transaction
                .execute(
                    "INSERT INTO works(id, updated_at, data) VALUES (?1, ?2, ?3)
                     ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, data=excluded.data",
                    params![work, text(stored, "updated_at")?, stored.to_string()],
                )
                .map_err(failed)?;
            for message in payload
                .get("messages")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let sequence = message
                    .get("sequence")
                    .and_then(Value::as_i64)
                    .ok_or("消息缺少序号")?;
                transaction
                    .execute(
                        "INSERT INTO messages(id, work_id, sequence, data) VALUES (?1, ?2, ?3, ?4)
                         ON CONFLICT(id) DO NOTHING",
                        params![text(message, "id")?, work, sequence, message.to_string()],
                    )
                    .map_err(failed)?;
            }
            for job in payload
                .get("jobs")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                transaction
                    .execute(
                        "INSERT INTO jobs(id, work_id, data) VALUES (?1, ?2, ?3)
                         ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        params![text(job, "id")?, work, job.to_string()],
                    )
                    .map_err(failed)?;
            }
            for version in payload
                .get("versions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                transaction
                    .execute(
                        "INSERT INTO versions(id, work_id, data) VALUES (?1, ?2, ?3) ON CONFLICT(id) DO NOTHING",
                        params![text(version, "id")?, work, version.to_string()],
                    )
                    .map_err(failed)?;
            }
            transaction.commit().map_err(failed)?;
            Ok(Value::Null)
        }
        "remove" => {
            let work = text(payload, "work")?;
            let transaction = connection.transaction().map_err(failed)?;
            for table in ["messages", "jobs", "versions"] {
                transaction
                    .execute(
                        &format!("DELETE FROM {table} WHERE work_id=?1"),
                        params![work],
                    )
                    .map_err(failed)?;
            }
            transaction
                .execute("DELETE FROM works WHERE id=?1", params![work])
                .map_err(failed)?;
            transaction.commit().map_err(failed)?;
            Ok(Value::Null)
        }
        _ => Err("未知的本地会话操作".into()),
    }
}

/// 前端会话存储入口；只读写应用数据目录下的固定库文件，不访问后端或任意用户路径。
#[tauri::command]
pub fn remotion_sessions(
    app: tauri::AppHandle,
    operation: String,
    payload: Value,
) -> Result<Value, String> {
    let path = app
        .path()
        .app_data_dir()
        .map_err(|_| "读取应用目录失败")?
        .join("data/remotion/sessions.sqlite3");
    operate(&path, &operation, &payload)
}

#[cfg(test)]
mod tests {
    //! 临时目录验证建库、设备 ID、事务写入、分页与删除；执行 cargo test --locked --lib remotion_sessions。
    use super::*;

    /// 每个用例使用独立临时库，结束时删除目录。
    struct Scratch(std::path::PathBuf);
    impl Scratch {
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("imv-sessions-{}", uuid::Uuid::new_v4())))
        }
        fn path(&self) -> std::path::PathBuf {
            self.0.join("sessions.sqlite3")
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// 一次保存：作品、两条消息、一个任务与一个版本索引。
    fn change(work: &str, updated_at: &str) -> Value {
        json!({
            "work": {"work": {"id": work, "current_version_id": null}, "title": "标题", "updated_at": updated_at, "cursor": 2,
                     "job": {"id": format!("{work}-job")}},
            "messages": [
                {"id": format!("{work}-m1"), "sequence": 1, "text": "一"},
                {"id": format!("{work}-m2"), "sequence": 2, "text": "二"}
            ],
            "jobs": [{"id": format!("{work}-job"), "status": "running"}],
            "versions": [{"id": format!("{work}-v1"), "work_id": work, "job_id": format!("{work}-job")}]
        })
    }

    /// 设备 ID 首次生成后保持不变，库文件按需创建。
    #[test]
    fn device_id_is_stable() {
        let scratch = Scratch::new();
        let first = operate(&scratch.path(), "device", &json!({})).unwrap();
        assert_eq!(
            operate(&scratch.path(), "device", &json!({})).unwrap(),
            first
        );
        assert!(uuid::Uuid::parse_str(first.as_str().unwrap()).is_ok());
    }

    /// 保存后按活动时间列出会话，消息从新到旧分页，任务只按所属作品读取。
    #[test]
    fn save_and_read_back() {
        let scratch = Scratch::new();
        operate(
            &scratch.path(),
            "save",
            &change("a", "2026-10-01T00:00:00Z"),
        )
        .unwrap();
        operate(
            &scratch.path(),
            "save",
            &change("b", "2026-10-02T00:00:00Z"),
        )
        .unwrap();
        let works = operate(&scratch.path(), "works", &json!({})).unwrap();
        assert_eq!(works[0]["work"]["id"], "b");
        assert_eq!(works[1]["work"]["id"], "a");
        let page = operate(
            &scratch.path(),
            "messages",
            &json!({"work": "a", "before": 2, "limit": 5}),
        )
        .unwrap();
        assert_eq!(page, json!([{"id": "a-m1", "sequence": 1, "text": "一"}]));
        let jobs = operate(
            &scratch.path(),
            "jobs",
            &json!({"work": "a", "ids": ["a-job", "b-job"]}),
        )
        .unwrap();
        assert_eq!(jobs, json!([{"id": "a-job", "status": "running"}]));
        assert_eq!(
            operate(&scratch.path(), "work", &json!({"id": "missing"})).unwrap(),
            Value::Null
        );
    }

    /// 删除只移除目标会话的全部记录，其他会话保持不变。
    #[test]
    fn remove_one_work() {
        let scratch = Scratch::new();
        operate(
            &scratch.path(),
            "save",
            &change("a", "2026-10-01T00:00:00Z"),
        )
        .unwrap();
        operate(
            &scratch.path(),
            "save",
            &change("b", "2026-10-02T00:00:00Z"),
        )
        .unwrap();
        operate(&scratch.path(), "remove", &json!({"work": "a"})).unwrap();
        assert_eq!(
            operate(&scratch.path(), "work", &json!({"id": "a"})).unwrap(),
            Value::Null
        );
        let page = operate(
            &scratch.path(),
            "messages",
            &json!({"work": "a", "limit": 5}),
        )
        .unwrap();
        assert_eq!(page, json!([]));
        assert_eq!(
            operate(&scratch.path(), "works", &json!({}))
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    /// 缺少必需字段的保存整体失败，不留下部分写入。
    #[test]
    fn invalid_save_writes_nothing() {
        let scratch = Scratch::new();
        let mut bad = change("a", "2026-10-01T00:00:00Z");
        bad["messages"][1]
            .as_object_mut()
            .unwrap()
            .remove("sequence");
        assert!(operate(&scratch.path(), "save", &bad).is_err());
        assert_eq!(
            operate(&scratch.path(), "work", &json!({"id": "a"})).unwrap(),
            Value::Null
        );
    }
}
