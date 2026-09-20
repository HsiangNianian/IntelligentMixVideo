//! 离线模板库：在应用数据目录的 data/template 保存 JSON，文件锁串行化读写，原子替换保留旧数据。

use chrono::Utc;
use serde_json::{json, Value};
use std::{fs, io::Write, path::Path};
use tauri::Manager;
use uuid::Uuid;

/// 校验完整编辑草稿并从随包目录生成效果快照；不接受调用方提供的路径、时间或渲染参数。
fn validate_legacy(mut draft: Value, allow_empty: bool) -> Result<Value, String> {
    if draft.as_object().is_none_or(|fields| fields.len() != 4) {
        return Err("模板字段不完整或包含未知字段".into());
    }
    for (key, max, required) in [("name", 100, true), ("description", 1000, false)] {
        let text = draft[key]
            .as_str()
            .ok_or("名称和说明必须是文字")?
            .trim()
            .to_owned();
        if (required && text.is_empty()) || text.chars().count() > max {
            return Err("模板名称或说明长度不合法".into());
        }
        draft[key] = json!(text);
    }
    number(&draft["transition_duration_seconds"], 0.1, 3.0, false)?;
    let editor = &draft["editor"];
    if editor.as_object().is_none_or(|fields| fields.len() != 33) {
        return Err("编辑配置字段不完整或包含未知字段".into());
    }
    let catalog: Value = serde_json::from_str(include_str!(
        "../../../server/src/server/template/sdk_catalog.json"
    ))
    .map_err(|_| "内置效果目录损坏")?;
    let motions: Vec<Value> = serde_json::from_str(include_str!(
        "../../../server/src/server/template/motions.json"
    ))
    .map_err(|_| "内置动画目录损坏")?;
    let mut effects = Vec::new();
    for role in ["title", "subtitle", "bubble"] {
        let text_key = if role == "bubble" { "bubbleText" } else { role };
        let max = match role {
            "title" => 60,
            "subtitle" => 100,
            _ => 40,
        };
        if editor[text_key]
            .as_str()
            .ok_or("示例文字必须是文字")?
            .chars()
            .count()
            > max
        {
            return Err("示例文字过长".into());
        }
        for (suffix, min, max, integer) in [
            ("Size", 12., 120., true),
            ("X", 0., 100., false),
            ("Y", 0., 100., false),
            ("InDuration", 0.1, 3., false),
            ("OutDuration", 0.1, 3., false),
        ] {
            number(&editor[format!("{role}{suffix}")], min, max, integer)?;
        }
        let mut selected = Vec::new();
        for motion in ["In", "Out", "Loop"] {
            let value = editor[format!("{role}{motion}")]
                .as_str()
                .ok_or("动画 ID 必须是文字")?;
            selected.push(!value.is_empty());
            if !value.is_empty() {
                let asset = motions
                    .iter()
                    .find(|item| item["id"] == value && item["category"] == motion.to_lowercase())
                    .ok_or("动画不在对应目录中")?;
                if !effects.contains(asset) {
                    effects.push(asset.clone());
                }
            }
        }
        if selected[2] && (selected[0] || selected[1]) {
            return Err("循环与入场、出场动画不能同时使用".into());
        }
    }
    for (key, category, parameter) in [
        ("titleFlower", "flower", "EffectColorStyle"),
        ("subtitleFlower", "flower", "EffectColorStyle"),
        ("bubble", "bubble", "BubbleStyleId"),
        ("filter", "filter", "SubType"),
        ("vfx", "vfx/normal", "SubType"),
        ("transition", "transition/normal", "SubType"),
    ] {
        let value = editor[key].as_str().ok_or("效果 ID 必须是文字")?;
        if value.is_empty() {
            continue;
        }
        let code = value
            .strip_prefix(&format!("{category}/"))
            .ok_or("效果分类不正确")?;
        if !catalog["categories"][category]
            .as_array()
            .ok_or("内置效果目录损坏")?
            .contains(&json!(code))
        {
            return Err("效果不在对应目录中".into());
        }
        let asset = json!({"id": value, "category": category, "name": code, "effect_id": code, "parameters": {parameter: code}, "preview_url": ""});
        if !effects.contains(&asset) {
            effects.push(asset);
        }
    }
    if effects.is_empty() && !allow_empty {
        return Err("请至少选择一个效果".into());
    }
    draft["effect_ids"] = effects.iter().map(|item| item["id"].clone()).collect();
    draft["effects"] = json!(effects);
    Ok(draft)
}

/// 多轨保存校验时间规则、唯一 ID 与所属对象，模板不包含视频信息。
fn validate(mut draft: Value) -> Result<Value, String> {
    let object = draft.as_object_mut().ok_or("模板须为对象")?;
    let tracks = object.remove("tracks").filter(|value| !value.is_null());
    let mut result = validate_legacy(draft, tracks.is_some())?;
    if let Some(tracks) = tracks {
        let items = tracks.as_array().ok_or("轨道须为数组")?;
        if items.len() > 100 {
            return Err("轨道数量不能超过 100".into());
        }
        let mut ids = std::collections::HashSet::new();
        let mut effects: Vec<Value> = Vec::new();
        let mut transitions = 0;
        for track in items {
            if track
                .as_object()
                .is_none_or(|fields| fields.len() != 6 || !fields.contains_key("duration"))
            {
                return Err("轨道字段不完整".into());
            }
            let id = track["id"].as_str().ok_or("轨道 ID 无效")?;
            if id.is_empty()
                || id.len() > 100
                || !id
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
                || !ids.insert(id)
            {
                return Err("轨道 ID 无效或重复".into());
            }
            let target = track["target"].as_str().ok_or("轨道对象无效")?;
            if !["title", "subtitle", "bubble", "filter", "vfx", "transition"].contains(&target) {
                return Err("轨道对象无效".into());
            }
            number(&track["start"], 0., f64::MAX, false)?;
            let start = track["start"].as_f64().ok_or("轨道时间无效")?;
            let mode = track["start_mode"].as_str().ok_or("开始方式无效")?;
            if !["seconds", "percent"].contains(&mode) || (mode == "percent" && start >= 100.) {
                return Err("开始方式或百分比无效".into());
            }
            if !track["duration"].is_null() {
                number(&track["duration"], f64::MIN_POSITIVE, f64::MAX, false)?;
            }
            if target == "transition" {
                transitions += 1;
                let length = track["duration"].as_f64().ok_or("转场需要固定持续时间")?;
                if transitions > 1 || start <= 0. || !(0.1..=3.).contains(&length) {
                    return Err("转场位置、时长或数量无效".into());
                }
            }
            let validated = validate_legacy(
                json!({
                    "name": result["name"], "description": "", "editor": track["editor"],
                    "transition_duration_seconds": 0.5
                }),
                true,
            )?;
            let editor = &validated["editor"];
            let mut allowed = vec![target.to_owned()];
            if ["title", "subtitle", "bubble"].contains(&target) {
                allowed = vec![if target == "bubble" {
                    "bubble".into()
                } else {
                    format!("{target}Flower")
                }];
                for suffix in ["In", "Out", "Loop"] {
                    allowed.push(format!("{target}{suffix}"));
                }
            } else if editor[target] == "" {
                return Err("轨道缺少效果".into());
            }
            for (role, field) in [
                ("title", "title"),
                ("subtitle", "subtitle"),
                ("bubble", "bubbleText"),
            ] {
                let content = editor[field].as_str().ok_or("文字无效")?;
                if (role != target && !content.is_empty())
                    || (role == target && content.trim().is_empty())
                {
                    return Err("轨道文字与对象不一致".into());
                }
            }
            for key in [
                "titleFlower",
                "subtitleFlower",
                "bubble",
                "filter",
                "vfx",
                "transition",
                "titleIn",
                "titleOut",
                "titleLoop",
                "subtitleIn",
                "subtitleOut",
                "subtitleLoop",
                "bubbleIn",
                "bubbleOut",
                "bubbleLoop",
            ] {
                if editor[key] != "" && !allowed.contains(&key.to_owned()) {
                    return Err("轨道包含其他对象的效果".into());
                }
            }
            for asset in validated["effects"].as_array().ok_or("效果目录无效")? {
                if !effects.contains(asset) {
                    effects.push(asset.clone());
                }
            }
        }
        if effects.is_empty() {
            return Err("请至少选择一个效果".into());
        }
        result["effect_ids"] = effects.iter().map(|item| item["id"].clone()).collect();
        result["effects"] = json!(effects);
        result["tracks"] = tracks;
    }
    Ok(result)
}

/// IPC 数值边界：拒绝 null、非有限数、越界和小数字号。
fn number(value: &Value, min: f64, max: f64, integer: bool) -> Result<(), String> {
    match value.as_f64() {
        Some(n) if n.is_finite() && n >= min && n <= max && (!integer || n.fract() == 0.) => Ok(()),
        _ => Err("字号、位置或时长不合法".into()),
    }
}

/// 从磁盘读取后检查结构；损坏时明确失败，不能当成空库覆盖。
fn read(directory: &Path) -> Result<Vec<Value>, String> {
    let bytes = match fs::read(directory.join("templates.json")) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("读取本地模板失败：{error}")),
    };
    let records: Vec<Value> =
        serde_json::from_slice(&bytes).map_err(|_| "本地模板文件损坏，请先恢复备份")?;
    let mut ids = std::collections::HashSet::new();
    let mut names = std::collections::HashSet::new();
    for record in &records {
        let id = record["template_id"].as_str().ok_or("本地模板 ID 缺失")?;
        Uuid::parse_str(id).map_err(|_| "本地模板 ID 无效")?;
        for key in ["created_at", "updated_at"] {
            chrono::DateTime::parse_from_rfc3339(record[key].as_str().ok_or("本地模板时间缺失")?)
                .map_err(|_| "本地模板时间无效")?;
        }
        let mut draft = record.clone();
        for key in [
            "template_id",
            "created_at",
            "updated_at",
            "effects",
            "effect_ids",
        ] {
            draft.as_object_mut().ok_or("本地模板损坏")?.remove(key);
        }
        let validated = validate(draft)?;
        if record["effects"] != validated["effects"]
            || record["effect_ids"] != validated["effect_ids"]
            || !ids.insert(id)
            || !names.insert(record["name"].as_str().ok_or("本地模板名称无效")?)
        {
            return Err("本地模板文件存在重复或无效数据，请先恢复备份".into());
        }
    }
    Ok(records)
}

/// 所有操作在同一文件锁下执行；临时文件刷盘后替换，失败保留原文件且允许重试。
fn operate(
    directory: &Path,
    operation: &str,
    id: Option<&str>,
    draft: Option<Value>,
) -> Result<Value, String> {
    if !["list", "get", "save", "delete"].contains(&operation) {
        return Err("未知本地模板操作".into());
    }
    if let Some(id) = id {
        Uuid::parse_str(id).map_err(|_| "模板 ID 无效")?;
    }
    if matches!(operation, "get" | "delete") && id.is_none() {
        return Err("缺少模板 ID".into());
    }
    fs::create_dir_all(directory).map_err(|error| format!("无法创建本地模板目录：{error}"))?;
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(directory.join(".lock"))
        .map_err(|error| format!("无法打开模板锁：{error}"))?;
    lock.try_lock()
        .map_err(|_| "本地模板正在被其他操作使用，请重试")?;
    let mut records = read(directory)?;
    let index = id.and_then(|id| records.iter().position(|item| item["template_id"] == id));
    if id.is_some() && index.is_none() {
        return Err("模板不存在".into());
    }
    let result = match operation {
        "list" => return Ok(json!(records)),
        "get" => return Ok(records[index.ok_or("模板不存在")?].clone()),
        "delete" => {
            records.remove(index.ok_or("模板不存在")?);
            Value::Null
        }
        "save" => {
            let mut saved = validate(draft.ok_or("缺少模板配置")?)?;
            if records
                .iter()
                .enumerate()
                .any(|(i, item)| Some(i) != index && item["name"] == saved["name"])
            {
                return Err("模板名称已存在，请使用其他名称".into());
            }
            let now = Utc::now().to_rfc3339();
            saved["template_id"] = json!(id
                .map(str::to_owned)
                .unwrap_or_else(|| Uuid::new_v4().to_string()));
            saved["created_at"] = index
                .map(|i| records[i]["created_at"].clone())
                .unwrap_or(json!(now));
            saved["updated_at"] = json!(now);
            if let Some(i) = index {
                records.remove(i);
            }
            records.insert(0, saved.clone());
            saved
        }
        _ => unreachable!(),
    };
    let temporary = directory.join("templates.json.tmp");
    let write = || -> std::io::Result<()> {
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&serde_json::to_vec_pretty(&records)?)?;
        file.sync_all()?;
        fs::rename(&temporary, directory.join("templates.json"))
    };
    if let Err(error) = write() {
        let _ = fs::remove_file(&temporary);
        return Err(format!("保存本地模板失败，原数据已保留：{error}"));
    }
    Ok(result)
}

/// 只允许在固定应用数据目录操作模板；不调用 Python 服务或访问用户传入的任意路径。
#[tauri::command]
pub fn local_templates(
    app: tauri::AppHandle,
    operation: String,
    id: Option<String>,
    draft: Option<Value>,
) -> Result<Value, String> {
    let directory = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("data/template");
    operate(&directory, &operation, id.as_deref(), draft)
}

#[cfg(test)]
mod tests {
    //! 使用临时目录验证离线增删改查、校验、锁冲突和失败保留；cargo test --locked。
    use super::*;

    /// 每例独立目录，退出时清理文件，不接触真实应用数据。
    struct Directory(std::path::PathBuf);
    impl Directory {
        /// 以随机目录隔离并行测试。
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("imv-template-test-{}", Uuid::new_v4())))
        }
    }
    impl Drop for Directory {
        /// 即使断言失败也清理该用例的临时数据。
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    /// 完整合法草稿，明确覆盖三种文字、所有位置和时长字段。
    fn draft(name: &str) -> Value {
        let mut editor = json!({"title": "标题", "subtitle": "字幕", "bubbleText": "气泡", "titleFlower": "", "subtitleFlower": "", "bubble": "", "filter": "", "vfx": "", "transition": ""});
        for role in ["title", "subtitle", "bubble"] {
            for (suffix, value) in [
                ("Size", 40.),
                ("X", 50.),
                ("Y", 50.),
                ("InDuration", 0.5),
                ("OutDuration", 0.5),
            ] {
                editor[format!("{role}{suffix}")] = json!(value);
            }
            for suffix in ["In", "Out", "Loop"] {
                editor[format!("{role}{suffix}")] = json!("");
            }
        }
        editor["titleIn"] = json!("in/fade_in");
        json!({"name": name, "description": "  说明  ", "editor": editor, "transition_duration_seconds": 0.5})
    }

    #[test]
    /// 空库、保存、重新读取、更新、另存为及删除全部落在临时文件；同名不会覆盖。
    fn local_crud_survives_reload() {
        let dir = Directory::new();
        assert_eq!(operate(&dir.0, "list", None, None).unwrap(), json!([]));
        let first = operate(&dir.0, "save", None, Some(draft("  模板  "))).unwrap();
        let id = first["template_id"].as_str().unwrap();
        assert_eq!(first["name"], "模板");
        assert_eq!(first["description"], "说明");
        assert_eq!(
            first["effects"][0]["parameters"],
            json!({"AaiMotionInEffect": "fade_in"})
        );
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), first);
        assert!(operate(&dir.0, "save", None, Some(draft("模板")))
            .unwrap_err()
            .contains("名称已存在"));
        let updated = operate(&dir.0, "save", Some(id), Some(draft("重命名"))).unwrap();
        assert_eq!(updated["created_at"], first["created_at"]);
        let copy = operate(&dir.0, "save", None, Some(draft("副本"))).unwrap();
        assert_ne!(copy["template_id"], first["template_id"]);
        assert_eq!(
            operate(&dir.0, "list", None, None).unwrap(),
            json!([copy, updated])
        );
        assert!(operate(&dir.0, "save", Some(id), Some(draft("副本"))).is_err());
        operate(&dir.0, "delete", Some(id), None).unwrap();
        assert!(operate(&dir.0, "delete", Some(id), None).is_err());
        assert!(operate(&dir.0, "get", Some(id), None).is_err());
        assert!(operate(&dir.0, "save", Some(id), Some(draft("不能重建"))).is_err());
        assert_eq!(operate(&dir.0, "list", None, None).unwrap(), json!([copy]));
        assert!(!dir.0.join("templates.json.tmp").exists());
    }

    #[test]
    /// 无效配置和任意路径 ID 被拒绝，保留既有数据；有效数值边界允许保存。
    fn validates_input_without_writes() {
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(draft("保留"))).unwrap();
        for (pointer, value) in [
            ("/name", json!(" ")),
            ("/editor/titleSize", json!(12.5)),
            ("/editor/titleX", json!(101)),
            ("/editor/titleInDuration", Value::Null),
            ("/editor/titleIn", json!("in/unknown")),
            ("/editor/titleLoop", json!("loop/bounce")),
            ("/editor/titleFlower", json!("filter/fake")),
            ("/transition_duration_seconds", json!(0)),
            ("/editor/title", json!("题".repeat(61))),
        ] {
            let mut invalid = draft("错误");
            *invalid.pointer_mut(pointer).unwrap() = value;
            assert!(
                operate(&dir.0, "save", None, Some(invalid)).is_err(),
                "{pointer}"
            );
        }
        assert!(operate(&dir.0, "get", Some("../../other"), None).is_err());
        assert!(operate(&dir.0, "delete", None, None).is_err());
        assert!(operate(&dir.0, "unknown", None, None).is_err());
        assert_eq!(operate(&dir.0, "list", None, None).unwrap(), json!([saved]));
        let mut boundary = draft("边界");
        boundary["editor"]["titleSize"] = json!(12);
        boundary["editor"]["titleX"] = json!(100);
        boundary["transition_duration_seconds"] = json!(3);
        assert!(operate(&dir.0, "save", None, Some(boundary)).is_ok());
    }

    #[test]
    /// 时间规则从真实文件恢复，秒数不受预览限制，非法百分比或持续时间保留原记录。
    fn multiple_tracks_survive_reload() {
        let dir = Directory::new();
        let mut value = draft("多轨模板");
        let mut editor = value["editor"].clone();
        editor["subtitle"] = json!("");
        editor["bubbleText"] = json!("");
        value["tracks"] = json!([
            {"id": "title-a", "target": "title", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor},
            {"id": "title-b", "target": "title", "start_mode": "seconds", "start": 200, "duration": null, "editor": editor}
        ]);
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        assert_eq!(saved["tracks"], value["tracks"]);
        assert!(saved.get("media").is_none());
        assert_eq!(saved["effect_ids"], json!(["in/fade_in"]));
        for (field, invalid) in [
            ("duration", json!(0)),
            ("start", json!(100)),
            ("start_mode", json!("frames")),
            ("id", json!("title-b")),
            ("media", json!({})),
        ] {
            let mut rejected = value.clone();
            rejected["tracks"][0][field] = invalid;
            assert!(operate(&dir.0, "save", Some(id), Some(rejected)).is_err());
            assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        }
        value["tracks"].as_array_mut().unwrap().remove(0);
        let updated = operate(&dir.0, "save", Some(id), Some(value)).unwrap();
        assert_eq!(updated["tracks"].as_array().unwrap().len(), 1);
        assert_eq!(updated["tracks"][0]["id"], "title-b");
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), updated);
    }

    #[test]
    /// 文件锁竞争、临时写入失败和损坏 JSON 均可见报错，不覆盖原始文件，解除后可重试。
    fn failures_preserve_file_and_allow_retry() {
        let dir = Directory::new();
        operate(&dir.0, "save", None, Some(draft("保留"))).unwrap();
        let path = dir.0.join("templates.json");
        let original = fs::read(&path).unwrap();
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(dir.0.join(".lock"))
            .unwrap();
        lock.try_lock().unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("冲突")))
            .unwrap_err()
            .contains("其他操作"));
        drop(lock);
        fs::create_dir(dir.0.join("templates.json.tmp")).unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("失败"))).is_err());
        assert_eq!(fs::read(&path).unwrap(), original);
        fs::remove_dir(dir.0.join("templates.json.tmp")).unwrap();
        fs::write(&path, b"broken json").unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("禁止覆盖"))).is_err());
        assert_eq!(fs::read(&path).unwrap(), b"broken json");
        fs::write(&path, original).unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("恢复"))).is_ok());
    }
}
