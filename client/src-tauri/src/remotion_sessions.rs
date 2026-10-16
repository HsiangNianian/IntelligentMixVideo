//! 字效会话的本地 SQLite：聊天消息、任务公开状态与本机设备 ID 只保存在使用者电脑。
//!
//! 库文件位于应用数据目录的 `data/remotion/sessions.sqlite3`。命令是异步的，读写在阻塞线程池执行，
//! 不占用界面主线程；整个进程共用一个首次调用时打开的连接，表结构只在打开时按 `PRAGMA user_version`
//! 建立一次，Mutex 串行化各次调用。前端的 `localSessions.ts` 负责合并服务端事件，这里只按操作读写 JSON 行：
//! `save` 在一个事务内写入会话与本批记录，`remove` 删除一条会话的全部记录。会话、消息与任务都按前端
//! 传入的服务地址 `server` 归属，不同服务的同 ID 记录互不影响；设备 ID 全局唯一。每个操作的参数是固定结构，
//! 多出或缺少字段都会明确报错。

use rusqlite::{params, Connection, OptionalExtension};
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::Manager;

/// 当前表结构版本；以后改表时按 `PRAGMA user_version` 从旧版本逐级迁移。
const SCHEMA_VERSION: i64 = 1;

/// 版本 1 的表结构：会话记录以（服务地址, ID）为键；`data` 列保存前端定义的 JSON 文档。
const SCHEMA: &str = "
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE works (
        server TEXT NOT NULL, id TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY (server, id)
    );
    CREATE INDEX works_by_activity ON works(server, updated_at);
    CREATE TABLE messages (
        server TEXT NOT NULL, id TEXT NOT NULL, work_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        data TEXT NOT NULL, PRIMARY KEY (server, id)
    );
    CREATE INDEX messages_by_work ON messages(server, work_id, sequence);
    CREATE TABLE jobs (
        server TEXT NOT NULL, id TEXT NOT NULL, work_id TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY (server, id)
    );
    CREATE INDEX jobs_by_work ON jobs(server, work_id);
";

/// 进程内共享的会话库连接；首次调用时打开，打开失败则下次调用重试。
#[derive(Default)]
pub struct Sessions(Arc<Mutex<Option<Connection>>>);

/// `device` 不带参数。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Empty {}

/// `works`：列出一个服务下的全部会话。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorksQuery {
    server: String,
}

/// `work`：按 ID 读取一条会话。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkQuery {
    server: String,
    id: String,
}

/// `messages`：按序号从新到旧分页，`before` 为空时从最新开始。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MessagesQuery {
    server: String,
    work: String,
    before: Option<i64>,
    limit: i64,
}

/// `jobs`：读取属于该会话的指定任务。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JobsQuery {
    server: String,
    work: String,
    ids: Vec<String>,
}

/// `save`：会话行与本批新增或更新的消息、任务，原样保存前端的 JSON 文档。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Save {
    server: String,
    work: Value,
    messages: Vec<Value>,
    jobs: Vec<Value>,
}

/// `remove`：删除一条会话的全部记录。
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Remove {
    server: String,
    work: String,
}

/// 打开（必要时创建）会话库，并把表结构升级到当前版本。
fn open(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|_| "创建本地会话目录失败")?;
    }
    let mut connection = Connection::open(path).map_err(|_| "打开本地会话库失败")?;
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(|_| "设置本地会话库等待时间失败")?;
    connection
        .execute_batch("PRAGMA journal_mode=WAL;")
        .map_err(|_| "初始化本地会话库失败")?;
    migrate(&mut connection)?;
    Ok(connection)
}

/// 按 `user_version` 建表；更新版本客户端写过的库拒绝打开，避免旧代码误写新结构。
fn migrate(connection: &mut Connection) -> Result<(), String> {
    let failed = |_| "初始化本地会话库失败".to_string();
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(failed)?;
    if version > SCHEMA_VERSION {
        return Err("本地会话库来自更新版本的客户端，请升级客户端后再打开。".into());
    }
    if version == SCHEMA_VERSION {
        return Ok(());
    }
    let transaction = connection.transaction().map_err(failed)?;
    // 版本 0 是新库，或未发布的测试构建留下的旧表：重建会话表，保留设备 ID。
    transaction
        .execute_batch(&format!(
            "DROP TABLE IF EXISTS works; DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS jobs;
             DROP TABLE IF EXISTS versions; {SCHEMA} PRAGMA user_version={SCHEMA_VERSION};"
        ))
        .map_err(failed)?;
    transaction.commit().map_err(failed)
}

/// 按操作解析固定结构的参数；字段名或类型不符时报告原因。
fn parse<T: DeserializeOwned>(payload: Value) -> Result<T, String> {
    serde_json::from_value(payload).map_err(|error| format!("本地会话请求无效：{error}"))
}

/// 读取 JSON 文档里的字符串字段；缺少或类型不符时报告字段名。
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

/// 在已打开的连接上执行一次操作；所有写入都在事务内完成，失败时回滚。
fn operate(connection: &mut Connection, operation: &str, payload: Value) -> Result<Value, String> {
    let failed = |_| "读写本地会话库失败".to_string();
    match operation {
        "pending" => {
            parse::<Empty>(payload)?;
            // 全部服务的会话都参与门禁，不能只看当前打开的页签或服务；列出所属服务与标题，供用户找到并处理。
            let mut statement = connection.prepare(
                "SELECT server, COALESCE(json_extract(data, '$.title'), ''), COALESCE(json_extract(data, '$.deleting'), 0) = 1 FROM works WHERE COALESCE(json_extract(data, '$.job.status'), '') NOT IN ('succeeded', 'answered', 'needs_input', 'failed', 'cancelled', 'interrupted') OR json_extract(data, '$.deleting') = 1 ORDER BY server, updated_at DESC",
            ).map_err(failed)?;
            let rows = statement
                .query_map([], |row| {
                    Ok(json!({"server": row.get::<_, String>(0)?, "title": row.get::<_, String>(1)?, "deleting": row.get::<_, bool>(2)?}))
                })
                .map_err(failed)?
                .collect::<Result<Vec<Value>, _>>()
                .map_err(failed)?;
            Ok(Value::Array(rows))
        }
        "device" => {
            parse::<Empty>(payload)?;
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
            let query: WorksQuery = parse(payload)?;
            let mut statement = connection
                .prepare("SELECT data FROM works WHERE server=?1 ORDER BY updated_at DESC, id DESC")
                .map_err(failed)?;
            let rows = statement
                .query_map(params![query.server], |row| row.get(0))
                .and_then(|rows| rows.collect::<Result<Vec<String>, _>>())
                .map_err(failed)?;
            documents(rows)
        }
        "work" => {
            let query: WorkQuery = parse(payload)?;
            let data: Option<String> = connection
                .query_row(
                    "SELECT data FROM works WHERE server=?1 AND id=?2",
                    params![query.server, query.id],
                    |row| row.get(0),
                )
                .optional()
                .map_err(failed)?;
            data.map_or(Ok(Value::Null), |data| {
                serde_json::from_str(&data).map_err(|_| "本地会话记录损坏".to_string())
            })
        }
        "messages" => {
            let query: MessagesQuery = parse(payload)?;
            let mut statement = connection
                .prepare(
                    "SELECT data FROM messages WHERE server=?1 AND work_id=?2 AND sequence<?3
                     ORDER BY sequence DESC LIMIT ?4",
                )
                .map_err(failed)?;
            let rows = statement
                .query_map(
                    params![
                        query.server,
                        query.work,
                        query.before.unwrap_or(i64::MAX),
                        query.limit.clamp(1, 1000)
                    ],
                    |row| row.get(0),
                )
                .and_then(|rows| rows.collect::<Result<Vec<String>, _>>())
                .map_err(failed)?;
            documents(rows)
        }
        "jobs" => {
            let query: JobsQuery = parse(payload)?;
            let mut rows = Vec::new();
            for id in &query.ids {
                let data: Option<String> = connection
                    .query_row(
                        "SELECT data FROM jobs WHERE server=?1 AND id=?2 AND work_id=?3",
                        params![query.server, id, query.work],
                        |row| row.get(0),
                    )
                    .optional()
                    .map_err(failed)?;
                rows.extend(data);
            }
            documents(rows)
        }
        "save" => {
            let change: Save = parse(payload)?;
            let work = text(change.work.get("work").ok_or("本地会话请求缺少作品")?, "id")?;
            let transaction = connection.transaction().map_err(failed)?;
            transaction
                .execute(
                    "INSERT INTO works(server, id, updated_at, data) VALUES (?1, ?2, ?3, ?4)
                     ON CONFLICT(server, id) DO UPDATE SET updated_at=excluded.updated_at, data=excluded.data",
                    params![
                        change.server,
                        work,
                        text(&change.work, "updated_at")?,
                        change.work.to_string()
                    ],
                )
                .map_err(failed)?;
            for message in &change.messages {
                let sequence = message
                    .get("sequence")
                    .and_then(Value::as_i64)
                    .ok_or("消息缺少序号")?;
                transaction
                    .execute(
                        "INSERT INTO messages(server, id, work_id, sequence, data) VALUES (?1, ?2, ?3, ?4, ?5)
                         ON CONFLICT(server, id) DO NOTHING",
                        params![
                            change.server,
                            text(message, "id")?,
                            work,
                            sequence,
                            message.to_string()
                        ],
                    )
                    .map_err(failed)?;
            }
            for job in &change.jobs {
                transaction
                    .execute(
                        "INSERT INTO jobs(server, id, work_id, data) VALUES (?1, ?2, ?3, ?4)
                         ON CONFLICT(server, id) DO UPDATE SET data=excluded.data",
                        params![change.server, text(job, "id")?, work, job.to_string()],
                    )
                    .map_err(failed)?;
            }
            transaction.commit().map_err(failed)?;
            Ok(Value::Null)
        }
        "remove" => {
            let query: Remove = parse(payload)?;
            let transaction = connection.transaction().map_err(failed)?;
            for table in ["messages", "jobs"] {
                transaction
                    .execute(
                        &format!("DELETE FROM {table} WHERE server=?1 AND work_id=?2"),
                        params![query.server, query.work],
                    )
                    .map_err(failed)?;
            }
            transaction
                .execute(
                    "DELETE FROM works WHERE server=?1 AND id=?2",
                    params![query.server, query.work],
                )
                .map_err(failed)?;
            transaction.commit().map_err(failed)?;
            Ok(Value::Null)
        }
        _ => Err("未知的本地会话操作".into()),
    }
}

/// 在共享连接上执行一次操作；尚未打开时先在 `path` 打开并建表，之后的调用不再重复打开。
fn run(
    shared: &Mutex<Option<Connection>>,
    path: &Path,
    operation: &str,
    payload: Value,
) -> Result<Value, String> {
    let mut connection = shared.lock().unwrap_or_else(|error| error.into_inner());
    if connection.is_none() {
        *connection = Some(open(path)?);
    }
    operate(connection.as_mut().expect("连接已打开"), operation, payload)
}

/// 前端会话存储入口；只读写应用数据目录下的固定库文件，不访问后端或任意用户路径。
#[tauri::command]
pub async fn remotion_sessions(
    app: tauri::AppHandle,
    operation: String,
    payload: Value,
) -> Result<Value, String> {
    let path = app
        .path()
        .app_data_dir()
        .map_err(|_| "读取应用目录失败")?
        .join("data/remotion/sessions.sqlite3");
    let shared = app.state::<Sessions>().0.clone();
    tauri::async_runtime::spawn_blocking(move || run(&shared, &path, &operation, payload))
        .await
        .map_err(|_| "本地会话库调用失败".to_string())?
}

#[cfg(test)]
mod tests {
    //! 临时目录验证建库与版本、共享连接、设备 ID、事务写入、分页与删除，以及前端实际发送的参数结构；
    //! 执行 cargo test --locked --lib remotion_sessions。
    use super::*;

    /// 每个用例使用独立临时库，结束时删除目录。
    struct Scratch(std::path::PathBuf);
    impl Scratch {
        /// 在系统临时目录下分配一个尚不存在的目录。
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("imv-sessions-{}", uuid::Uuid::new_v4())))
        }
        /// 库文件路径；目录由 `open` 按需创建。
        fn path(&self) -> std::path::PathBuf {
            self.0.join("sessions.sqlite3")
        }
        /// 打开该临时库。
        fn open(&self) -> Connection {
            open(&self.path()).unwrap()
        }
    }
    impl Drop for Scratch {
        /// 用例结束删除临时目录。
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// 测试默认使用的服务地址。
    const SERVER: &str = "http://api.test:8000";

    /// 一次保存：作品、两条消息与一个任务。
    fn change(work: &str, updated_at: &str) -> Value {
        json!({
            "server": SERVER,
            "work": {"work": {"id": work, "current_version_id": null}, "title": "标题", "updated_at": updated_at, "cursor": 2,
                     "job": {"id": format!("{work}-job")}},
            "messages": [
                {"id": format!("{work}-m1"), "sequence": 1, "text": "一"},
                {"id": format!("{work}-m2"), "sequence": 2, "text": "二"}
            ],
            "jobs": [{"id": format!("{work}-job"), "status": "running"}]
        })
    }

    /// 不同服务的后台会话和未知状态都阻止更新，终态与等用户回答的会话不阻止。
    #[test]
    fn pending_checks_all_servers_and_unknown_states() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        for (i, status) in ["queued", "running", "succeeded", "needs_input", "unknown"]
            .iter()
            .enumerate()
        {
            let mut value = change(&format!("w{i}"), "2026-10-16T00:00:00Z");
            value["server"] = json!(format!("http://server-{i}.test"));
            value["work"]["job"]["status"] = json!(status);
            operate(&mut connection, "save", value).unwrap();
        }
        let pending = operate(&mut connection, "pending", json!({})).unwrap();
        let servers: Vec<&str> = pending
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["server"].as_str().unwrap())
            .collect();
        assert_eq!(
            servers,
            [
                "http://server-0.test",
                "http://server-1.test",
                "http://server-4.test"
            ]
        );
        assert_eq!(pending[0]["deleting"], json!(false));
        assert!(operate(&mut connection, "pending", json!({"server":SERVER})).is_err());
    }

    /// 设备 ID 首次生成后保持不变，库文件按需创建。
    #[test]
    fn device_id_is_stable() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        let first = operate(&mut connection, "device", json!({})).unwrap();
        assert_eq!(
            operate(&mut connection, "device", json!({})).unwrap(),
            first
        );
        assert!(uuid::Uuid::parse_str(first.as_str().unwrap()).is_ok());
    }

    /// 新库写入当前表结构版本；重新打开不重建表，已保存的会话仍在。
    #[test]
    fn schema_is_created_once_with_a_version() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        operate(&mut connection, "save", change("a", "2026-10-01T00:00:00Z")).unwrap();
        drop(connection);
        let mut connection = scratch.open();
        let version: i64 = connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap();
        assert_eq!(version, SCHEMA_VERSION);
        assert_ne!(
            operate(
                &mut connection,
                "work",
                json!({"server": SERVER, "id": "a"})
            )
            .unwrap(),
            Value::Null
        );
    }

    /// 更新版本客户端写过的库拒绝打开，不用旧结构读写。
    #[test]
    fn newer_schema_is_refused() {
        let scratch = Scratch::new();
        scratch
            .open()
            .execute_batch(&format!("PRAGMA user_version={}", SCHEMA_VERSION + 1))
            .unwrap();
        assert!(open(&scratch.path()).unwrap_err().contains("更新版本"));
    }

    /// 未发布测试构建留下的无版本旧表被重建为当前结构，设备 ID 保留。
    #[test]
    fn unversioned_tables_are_rebuilt() {
        let scratch = Scratch::new();
        std::fs::create_dir_all(&scratch.0).unwrap();
        let old = Connection::open(scratch.path()).unwrap();
        old.execute_batch(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
             INSERT INTO meta VALUES ('device_id', 'old-device');
             CREATE TABLE works (id TEXT PRIMARY KEY, data TEXT NOT NULL);
             CREATE TABLE versions (id TEXT PRIMARY KEY, data TEXT NOT NULL);",
        )
        .unwrap();
        drop(old);
        let mut connection = scratch.open();
        operate(&mut connection, "save", change("a", "2026-10-01T00:00:00Z")).unwrap();
        assert_eq!(
            operate(&mut connection, "device", json!({})).unwrap(),
            json!("old-device")
        );
    }

    /// 共享连接只在第一次调用时打开：之后即使路径不可用也继续使用已打开的连接。
    #[test]
    fn shared_connection_is_opened_once() {
        let scratch = Scratch::new();
        let shared = Mutex::new(None);
        let device = run(&shared, &scratch.path(), "device", json!({})).unwrap();
        let unusable = scratch
            .path()
            .join("not-a-directory")
            .join("sessions.sqlite3");
        assert_eq!(
            run(&shared, &unusable, "device", json!({})).unwrap(),
            device
        );
        // 打开失败不留下半初始化的连接，下次调用重试。
        let fresh = Mutex::new(None);
        assert!(run(&fresh, &unusable, "device", json!({})).is_err());
        assert!(fresh.lock().unwrap().is_none());
    }

    /// 保存后按活动时间列出会话，消息从新到旧分页，任务只按所属作品读取。
    #[test]
    fn save_and_read_back() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        operate(&mut connection, "save", change("a", "2026-10-01T00:00:00Z")).unwrap();
        operate(&mut connection, "save", change("b", "2026-10-02T00:00:00Z")).unwrap();
        let works = operate(&mut connection, "works", json!({"server": SERVER})).unwrap();
        assert_eq!(works[0]["work"]["id"], "b");
        assert_eq!(works[1]["work"]["id"], "a");
        let page = operate(
            &mut connection,
            "messages",
            json!({"server": SERVER, "work": "a", "before": 2, "limit": 5}),
        )
        .unwrap();
        assert_eq!(page, json!([{"id": "a-m1", "sequence": 1, "text": "一"}]));
        let jobs = operate(
            &mut connection,
            "jobs",
            json!({"server": SERVER, "work": "a", "ids": ["a-job", "b-job"]}),
        )
        .unwrap();
        assert_eq!(jobs, json!([{"id": "a-job", "status": "running"}]));
        assert_eq!(
            operate(
                &mut connection,
                "work",
                json!({"server": SERVER, "id": "missing"})
            )
            .unwrap(),
            Value::Null
        );
    }

    /// 删除只移除目标会话的全部记录，其他会话保持不变。
    #[test]
    fn remove_one_work() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        operate(&mut connection, "save", change("a", "2026-10-01T00:00:00Z")).unwrap();
        operate(&mut connection, "save", change("b", "2026-10-02T00:00:00Z")).unwrap();
        operate(
            &mut connection,
            "remove",
            json!({"server": SERVER, "work": "a"}),
        )
        .unwrap();
        assert_eq!(
            operate(
                &mut connection,
                "work",
                json!({"server": SERVER, "id": "a"})
            )
            .unwrap(),
            Value::Null
        );
        let page = operate(
            &mut connection,
            "messages",
            json!({"server": SERVER, "work": "a", "before": null, "limit": 5}),
        )
        .unwrap();
        assert_eq!(page, json!([]));
        assert_eq!(
            operate(&mut connection, "works", json!({"server": SERVER}))
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    /// 不同服务的同 ID 会话互不可见：列表、读取与删除都只作用于请求的服务。
    #[test]
    fn works_are_scoped_by_server() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        let other = "http://other.test:9000";
        operate(&mut connection, "save", change("a", "2026-10-01T00:00:00Z")).unwrap();
        let mut elsewhere = change("a", "2026-10-02T00:00:00Z");
        elsewhere["server"] = json!(other);
        elsewhere["work"]["title"] = json!("另一个服务");
        operate(&mut connection, "save", elsewhere).unwrap();
        let works = operate(&mut connection, "works", json!({"server": other})).unwrap();
        assert_eq!(works.as_array().unwrap().len(), 1);
        assert_eq!(works[0]["title"], "另一个服务");
        operate(
            &mut connection,
            "remove",
            json!({"server": other, "work": "a"}),
        )
        .unwrap();
        assert_eq!(
            operate(&mut connection, "works", json!({"server": other})).unwrap(),
            json!([])
        );
        assert_eq!(
            operate(
                &mut connection,
                "work",
                json!({"server": SERVER, "id": "a"})
            )
            .unwrap()["title"],
            "标题"
        );
        let page = operate(
            &mut connection,
            "messages",
            json!({"server": SERVER, "work": "a", "before": null, "limit": 5}),
        )
        .unwrap();
        assert_eq!(page.as_array().unwrap().len(), 2);
        let jobs = operate(
            &mut connection,
            "jobs",
            json!({"server": SERVER, "work": "a", "ids": ["a-job"]}),
        )
        .unwrap();
        assert_eq!(jobs.as_array().unwrap().len(), 1);
    }

    /// 缺少必需字段的保存整体失败，不留下部分写入；多出或缺少参数字段同样拒绝。
    #[test]
    fn invalid_requests_write_nothing() {
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        let mut bad = change("a", "2026-10-01T00:00:00Z");
        bad["messages"][1]
            .as_object_mut()
            .unwrap()
            .remove("sequence");
        assert!(operate(&mut connection, "save", bad).is_err());
        assert_eq!(
            operate(
                &mut connection,
                "work",
                json!({"server": SERVER, "id": "a"})
            )
            .unwrap(),
            Value::Null
        );
        let mut extra = change("a", "2026-10-01T00:00:00Z");
        extra["versions"] = json!([]);
        assert!(operate(&mut connection, "save", extra)
            .unwrap_err()
            .contains("versions"));
        assert!(operate(
            &mut connection,
            "messages",
            json!({"server": SERVER, "work": "a"})
        )
        .is_err());
        assert!(operate(&mut connection, "unknown", json!({})).is_err());
    }

    /// 前端 `localSessions.ts` 实际发送的每种操作（由前端测试录制并逐字比对的同一份夹具）都能被解析并执行。
    #[test]
    fn frontend_payloads_match_the_contract() {
        let calls: Vec<Value> =
            serde_json::from_str(include_str!("../../tests/remotion-sessions-contract.json"))
                .unwrap();
        let scratch = Scratch::new();
        let mut connection = scratch.open();
        let mut operations = Vec::new();
        for call in calls {
            let operation = call["operation"].as_str().unwrap().to_string();
            let result = operate(&mut connection, &operation, call["payload"].clone());
            assert!(result.is_ok(), "{operation}: {result:?}");
            operations.push(operation);
        }
        for operation in [
            "device", "works", "work", "messages", "jobs", "save", "remove",
        ] {
            assert!(
                operations.iter().any(|item| item == operation),
                "{operation}"
            );
        }
    }
}
