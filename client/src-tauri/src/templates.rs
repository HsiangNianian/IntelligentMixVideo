//! 离线模板库：解析 Protobuf 保存请求，按通用设置的路径（默认 data/template/templates.json）保存 JSON，文件锁保护读写。

use chrono::Utc;
use prost::Message;
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};
use tauri::Manager;
use uuid::Uuid;

#[allow(dead_code)]
mod generated {
    //! 由 Buf 从共享模板协议生成的 Rust 消息类型。
    include!("generated/imv.template.v1.rs");
}

/// 按服务端 EffectTemplateEditor 的默认值补齐缺失字段，保留显式提交的数值。
fn editor_from_protobuf(editor: generated::EffectTemplateEditor) -> Value {
    let mut fields = json!({
        "title": editor.title.unwrap_or_else(|| "让每一帧 都有风格".into()),
        "subtitle": editor.subtitle.unwrap_or_else(|| "选择花字、滤镜和特效，看看组合效果".into()),
        "bubbleText": editor.bubble_text.unwrap_or_else(|| "超值特惠".into()),
        "titleFont": editor.title_font.unwrap_or_else(|| "Alibaba PuHuiTi".into()),
        "subtitleFont": editor.subtitle_font.unwrap_or_else(|| "Alibaba PuHuiTi".into()),
        "titleSize": editor.title_size.unwrap_or(40),
        "subtitleSize": editor.subtitle_size.unwrap_or(26),
        "bubbleSize": editor.bubble_size.unwrap_or(32),
        "titleX": editor.title_x.unwrap_or(50.),
        "titleY": editor.title_y.unwrap_or(8.),
        "subtitleX": editor.subtitle_x.unwrap_or(50.),
        "subtitleY": editor.subtitle_y.unwrap_or(82.),
        "bubbleX": editor.bubble_x.unwrap_or(25.),
        "bubbleY": editor.bubble_y.unwrap_or(32.),
        "titleFlower": editor.title_flower.unwrap_or_default(),
        "subtitleFlower": editor.subtitle_flower.unwrap_or_default(),
        "bubble": editor.bubble.unwrap_or_default(),
        "filter": editor.filter.unwrap_or_default(),
        "vfx": editor.vfx.unwrap_or_default(),
        "transition": editor.transition.unwrap_or_default(),
        "titleIn": editor.title_in.unwrap_or_default(),
        "titleOut": editor.title_out.unwrap_or_default(),
        "titleLoop": editor.title_loop.unwrap_or_default(),
        "subtitleIn": editor.subtitle_in.unwrap_or_default(),
        "subtitleOut": editor.subtitle_out.unwrap_or_default(),
        "subtitleLoop": editor.subtitle_loop.unwrap_or_default(),
        "bubbleIn": editor.bubble_in.unwrap_or_default(),
        "bubbleOut": editor.bubble_out.unwrap_or_default(),
        "bubbleLoop": editor.bubble_loop.unwrap_or_default(),
        "titleInDuration": editor.title_in_duration.unwrap_or(0.5),
        "titleOutDuration": editor.title_out_duration.unwrap_or(0.5),
        "subtitleInDuration": editor.subtitle_in_duration.unwrap_or(0.5),
        "subtitleOutDuration": editor.subtitle_out_duration.unwrap_or(0.5),
        "bubbleInDuration": editor.bubble_in_duration.unwrap_or(0.5),
        "bubbleOutDuration": editor.bubble_out_duration.unwrap_or(0.5),
    });
    fields["subtitleKeywordBold"] = json!(editor.subtitle_keyword_bold);
    fields["subtitleKeywordItalic"] = json!(editor.subtitle_keyword_italic);
    fields["subtitleKeywordUnderline"] = json!(editor.subtitle_keyword_underline);
    fields["subtitleKeywordStrikeout"] = json!(editor.subtitle_keyword_strikeout);
    fields["subtitleKeywordColor"] = json!(editor.subtitle_keyword_color);
    fields["subtitleKeywordSize"] = json!(editor.subtitle_keyword_size);
    fields["titleKeyword"] = json!(editor.title_keyword);
    fields["titleKeywordBold"] = json!(editor.title_keyword_bold);
    fields["titleKeywordItalic"] = json!(editor.title_keyword_italic);
    fields["titleKeywordUnderline"] = json!(editor.title_keyword_underline);
    fields["titleKeywordStrikeout"] = json!(editor.title_keyword_strikeout);
    fields["titleKeywordColor"] = json!(editor.title_keyword_color);
    fields["titleKeywordSize"] = json!(editor.title_keyword_size);
    fields
}

/// 底部字幕关键词的四个局部样式字段，供保存校验和旧记录读取共用。
const SUBTITLE_KEYWORD_FIELDS: [&str; 4] = [
    "subtitleKeywordBold",
    "subtitleKeywordItalic",
    "subtitleKeywordUnderline",
    "subtitleKeywordStrikeout",
];

/// 顶部标题关键词的四个局部样式字段，供保存校验和旧记录读取共用。
const TITLE_KEYWORD_FIELDS: [&str; 4] = [
    "titleKeywordBold",
    "titleKeywordItalic",
    "titleKeywordUnderline",
    "titleKeywordStrikeout",
];

/// 对象读取的样式字段前缀：关键词对象沿用所属标题或字幕的字段，其余对象就是自身。
fn text_role(target: &str) -> &str {
    match target {
        "title_keyword" => "title",
        "subtitle_keyword" => "subtitle",
        other => other,
    }
}

/// 将本地保存请求转换为现有 JSON 草稿结构，保留磁盘文件格式。
fn draft_from_protobuf(bytes: &[u8], id: Option<&str>) -> Result<Value, String> {
    let request =
        generated::SaveTemplateRequest::decode(bytes).map_err(|_| "本地模板 Protobuf 请求无效")?;
    if request.template_id.as_deref() != id {
        return Err("模板 ID 与保存请求不一致".into());
    }
    // Remotion 片段引用云端已发布的 Sprite，本地模板不支持；明确拒绝，而不是落到轨道白名单的笼统错误。
    // 任何轨道上的 Remotion 参数覆盖、需要 Remotion 字效的句中原位关键词同样拒绝，避免转换时被静默丢弃。
    if request.tracks.as_ref().is_some_and(|list| {
        list.tracks.iter().any(|track| {
            track.target == "remotion"
                || !track.sprite_id.is_empty()
                || track.text_target.is_some()
                || track.in_place == Some(true)
                || track
                    .editor
                    .as_ref()
                    .is_some_and(|editor| editor.remotion.is_some())
        })
    }) {
        return Err("本地模板不支持 Remotion 资产".into());
    }
    let tracks = request.tracks.map(|list| {
        list.tracks
            .into_iter()
            .map(|track| {
                let mut value = json!({
                    "id": track.id,
                    "target": track.target,
                    "duration": track.duration,
                    "editor": track.editor.map(editor_from_protobuf),
                });
                if !track.start_mode.is_empty() {
                    value["start_mode"] = json!(track.start_mode);
                }
                if let Some(start) = track.start {
                    value["start"] = json!(start);
                }
                if let Some(boundary) = track.transition_boundary {
                    value["transition_boundary"] = json!(boundary);
                }
                if let Some(language) = track.language {
                    value["language"] = json!(language);
                }
                if let Some(enabled) = track.enabled {
                    value["enabled"] = json!(enabled);
                }
                value
            })
            .collect::<Vec<_>>()
    });
    let mut draft = json!({
        "name": request.name,
        "description": request.description.unwrap_or_default(),
        "transition_duration_seconds": request.transition_duration_seconds.unwrap_or(1.0),
        "tracks": tracks,
        "effect_ids": request.effect_ids,
    });
    if let Some(mode) = request.transition_mode {
        draft["transition_mode"] = json!(mode);
    }
    if let Some(enabled) = request.transition_enabled {
        draft["transition_enabled"] = json!(enabled);
    }

    let mut expected_ids = Vec::new();
    for track in draft["tracks"].as_array().ok_or("轨道须为数组")? {
        for asset in validate_editor(&track["editor"])? {
            expected_ids.push(asset["id"].as_str().ok_or("效果 ID 无效")?.to_owned());
        }
    }
    expected_ids.sort();
    expected_ids.dedup();

    let mut declared_ids = draft["effect_ids"]
        .as_array()
        .ok_or("效果 ID 列表无效")?
        .iter()
        .map(|value| value.as_str().map(str::to_owned))
        .collect::<Option<Vec<_>>>()
        .ok_or("效果 ID 列表无效")?;
    declared_ids.sort();
    if declared_ids != expected_ids {
        return Err("所选特效与编辑配置不一致".into());
    }

    draft
        .as_object_mut()
        .ok_or("模板须为对象")?
        .remove("effect_ids");
    Ok(draft)
}

/// 校验对象编辑参数并从随包目录生成效果快照。
fn validate_editor(editor: &Value) -> Result<Vec<Value>, String> {
    if editor.as_object().is_none_or(|fields| fields.len() != 48) {
        return Err("编辑配置字段不完整或包含未知字段".into());
    }
    let fonts: Vec<Value> = serde_json::from_str(include_str!(
        "../../../server/src/server/template/fonts.json"
    ))
    .map_err(|_| "内置字体目录损坏")?;
    for key in ["titleFont", "subtitleFont"] {
        let font = editor[key].as_str().ok_or("字体须为文字")?;
        if !fonts.iter().any(|item| item["value"] == font) {
            return Err("字体不在支持的目录中".into());
        }
    }
    for key in SUBTITLE_KEYWORD_FIELDS
        .into_iter()
        .chain(TITLE_KEYWORD_FIELDS)
    {
        editor[key].as_bool().ok_or("关键词样式必须是布尔值")?;
    }
    let keyword = editor["titleKeyword"]
        .as_str()
        .ok_or("标题关键词须为文字")?;
    if keyword.chars().count() > 60 {
        return Err("标题关键词过长".into());
    }
    for field in ["titleKeywordColor", "subtitleKeywordColor"] {
        let color = editor[field].as_str().ok_or("关键词颜色须为文字")?;
        if !color.is_empty()
            && (color.len() != 7
                || !color.starts_with('#')
                || !color[1..].bytes().all(|value| value.is_ascii_hexdigit()))
        {
            return Err("关键词颜色须为 #RRGGBB".into());
        }
    }
    for field in ["titleKeywordSize", "subtitleKeywordSize"] {
        let size = editor[field].as_i64().ok_or("关键词字号须为整数")?;
        if size != 0 && !(12..=300).contains(&size) {
            return Err("关键词字号须为 12～300 的整数".into());
        }
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
            ("Size", 12., 300., true),
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
    Ok(effects)
}

/// 多轨保存校验时间规则、唯一 ID 与所属对象，模板不包含视频信息。
fn validate(mut draft: Value) -> Result<Value, String> {
    let object = draft.as_object().ok_or("模板须为对象")?;
    if object.len()
        != 4 + usize::from(object.contains_key("transition_mode"))
            + usize::from(object.contains_key("transition_enabled"))
        || ![
            "name",
            "description",
            "transition_duration_seconds",
            "tracks",
        ]
        .iter()
        .all(|key| object.contains_key(*key))
    {
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
    if draft
        .get("transition_enabled")
        .is_some_and(|value| !value.is_boolean())
    {
        return Err("转场开关必须是布尔值".into());
    }
    if let Some(mode) = draft.get("transition_mode") {
        if !matches!(mode.as_str(), Some("sequence" | "random")) {
            return Err("转场设置方式无效".into());
        }
    }
    let effects = {
        let items = draft["tracks"].as_array().ok_or("轨道须为数组")?;
        if items.len() > 100 {
            return Err("轨道数量不能超过 100".into());
        }
        let mut ids = std::collections::HashSet::new();
        let mut effects: Vec<Value> = Vec::new();
        let mut transitions = std::collections::HashSet::new();
        let mut singletons = std::collections::HashSet::new();
        for track in items {
            if track.as_object().is_none_or(|fields| {
                fields.len()
                    != (if track["target"] == "transition" {
                        5
                    } else {
                        6
                    }) + usize::from(fields.contains_key("language"))
                        + usize::from(fields.contains_key("enabled"))
                    || !fields.contains_key("duration")
            }) {
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
            let language = track.get("language").map_or(Some("zh"), Value::as_str);
            if !matches!(language, Some("zh" | "en"))
                || (language == Some("en") && !["title", "subtitle"].contains(&target))
            {
                return Err("文字语言无效".into());
            }
            let keyword = target.ends_with("_keyword");
            if track.get("enabled").is_some_and(|value| {
                !value.is_boolean() || (value == false && language != Some("en") && !keyword)
            }) {
                return Err("只有英文标题、英文字幕或关键词对象可以停用".into());
            }
            // 英文与关键词对象的时间都跟随所属的中文标题或字幕。
            if language == Some("en") || keyword {
                let chinese = items
                    .iter()
                    .find(|item| item["target"] == text_role(target) && item["language"] != "en")
                    .ok_or("英文或关键词需要对应的中文对象")?;
                if ["start_mode", "start", "duration"]
                    .iter()
                    .any(|key| track[*key] != chinese[*key])
                {
                    return Err("英文与关键词的时间必须跟随中文对象".into());
                }
            }
            if [
                "title",
                "subtitle",
                "vfx",
                "title_keyword",
                "subtitle_keyword",
            ]
            .contains(&target)
                && !singletons.insert((target, language))
            {
                return Err("同一语言的标题、字幕、关键词和 VFX 最多各一个".into());
            }
            if target != "transition" && track.get("transition_boundary").is_some() {
                return Err("只有转场可以设置视频交界处".into());
            }
            if ![
                "title",
                "subtitle",
                "title_keyword",
                "subtitle_keyword",
                "bubble",
                "filter",
                "vfx",
                "transition",
            ]
            .contains(&target)
            {
                return Err("轨道对象无效".into());
            }
            if !track["duration"].is_null() {
                number(&track["duration"], f64::MIN_POSITIVE, f64::MAX, false)?;
            }
            if target == "transition" {
                if track.get("start").is_some() || track.get("start_mode").is_some() {
                    return Err("转场只保存交界处与持续时间".into());
                }
                let boundary = track["transition_boundary"]
                    .as_u64()
                    .ok_or("转场必须指定视频交界处")?;
                let length = track["duration"].as_f64().ok_or("转场需要固定持续时间")?;
                if !(1..=100).contains(&boundary)
                    || !transitions.insert(boundary)
                    || !(0.1..=3.).contains(&length)
                {
                    return Err("转场位置、时长或数量无效".into());
                }
                if draft.get("transition_mode").is_some()
                    && track["duration"] != draft["transition_duration_seconds"]
                {
                    return Err("全部转场必须使用统一持续时间".into());
                }
            } else {
                number(&track["start"], 0., f64::MAX, false)?;
                let start = track["start"].as_f64().ok_or("轨道时间无效")?;
                let mode = track["start_mode"].as_str().ok_or("开始方式无效")?;
                if !["seconds", "percent"].contains(&mode) || (mode == "percent" && start >= 100.) {
                    return Err("开始方式或百分比无效".into());
                }
                if target == "subtitle"
                    && (mode != "seconds" || start != 0. || !track["duration"].is_null())
                {
                    return Err("底部字幕固定覆盖整个视频".into());
                }
            }
            let editor = &track["editor"];
            let validated = validate_editor(editor)?;
            if target != "subtitle"
                && (SUBTITLE_KEYWORD_FIELDS
                    .iter()
                    .any(|key| editor[*key] == true)
                    || editor["subtitleKeywordColor"] != ""
                    || editor["subtitleKeywordSize"] != 0)
            {
                return Err("只有底部字幕可以设置关键词样式".into());
            }
            if target != "title"
                && (editor["titleKeyword"] != ""
                    || TITLE_KEYWORD_FIELDS.iter().any(|key| editor[*key] == true)
                    || editor["titleKeywordColor"] != ""
                    || editor["titleKeywordSize"] != 0)
            {
                return Err("只有顶部标题可以设置标题关键词样式".into());
            }
            let role = text_role(target);
            let mut allowed = vec![target.to_owned()];
            if ["title", "subtitle", "bubble"].contains(&role) {
                allowed = vec![if role == "bubble" {
                    "bubble".into()
                } else {
                    format!("{role}Flower")
                }];
                for suffix in ["In", "Out", "Loop"] {
                    allowed.push(format!("{role}{suffix}"));
                }
            } else if editor[target] == "" {
                return Err("轨道缺少效果".into());
            }
            // 关键词对象没有示例文字：三段示例文字都必须为空。
            for (owner, field) in [
                ("title", "title"),
                ("subtitle", "subtitle"),
                ("bubble", "bubbleText"),
            ] {
                let content = editor[field].as_str().ok_or("文字无效")?;
                if (owner != target && !content.is_empty())
                    || (owner == target && content.trim().is_empty())
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
            for asset in &validated {
                if !effects.contains(asset) {
                    effects.push(asset.clone());
                }
            }
        }
        if items.is_empty() {
            return Err("请至少保留一个画面对象".into());
        }
        effects
    };
    draft["effect_ids"] = effects.iter().map(|item| item["id"].clone()).collect();
    draft["effects"] = json!(effects);
    Ok(draft)
}

/// IPC 数值边界：拒绝 null、非有限数、越界和小数字号。
fn number(value: &Value, min: f64, max: f64, integer: bool) -> Result<(), String> {
    match value.as_f64() {
        Some(n) if n.is_finite() && n >= min && n <= max && (!integer || n.fract() == 0.) => Ok(()),
        _ => Err("字号、位置或时长不合法".into()),
    }
}

/// 读取完整文件并校验记录标识，业务规则按操作检查指定记录。
fn read(path: &Path) -> Result<Vec<Value>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(format!("读取本地模板失败：{error}")),
    };
    let records: Vec<Value> =
        serde_json::from_slice(&bytes).map_err(|_| "本地模板文件损坏，请先恢复备份")?;
    let mut ids = std::collections::HashSet::new();
    let mut names = std::collections::HashSet::new();
    for record in &records {
        let id = record["template_id"]
            .as_str()
            .ok_or("本地模板 ID 缺失")?
            .to_owned();
        Uuid::parse_str(&id).map_err(|_| "本地模板 ID 无效")?;
        for key in ["created_at", "updated_at"] {
            chrono::DateTime::parse_from_rfc3339(record[key].as_str().ok_or("本地模板时间缺失")?)
                .map_err(|_| "本地模板时间无效")?;
        }
        if !ids.insert(id) || !names.insert(record["name"].as_str().ok_or("本地模板名称无效")?)
        {
            return Err("本地模板文件存在重复或无效数据，请先恢复备份".into());
        }
    }
    Ok(records)
}

/// 保存和读取有效模板共用必需对象检查。
fn require_complete(record: &Value) -> Result<(), String> {
    let tracks = record["tracks"].as_array().ok_or("轨道须为数组")?;
    if ["title", "subtitle"].iter().any(|target| {
        tracks
            .iter()
            .filter(|track| track["target"] == *target && track["language"] != "en")
            .count()
            != 1
    }) {
        return Err("保存模板需要标题和底部字幕各一个".into());
    }
    Ok(())
}

/// 验证单条配置和可信效果快照，调用者决定返回详情错误或列表状态。
fn validate_record(record: &Value) -> Result<Value, String> {
    let mut record = record.clone();
    // 样式开关的缺省值与 Python 字段声明一致，补充只发生在返回视图中。
    if let Some(tracks) = record["tracks"].as_array_mut() {
        for track in tracks {
            if let Some(editor) = track["editor"].as_object_mut() {
                for key in SUBTITLE_KEYWORD_FIELDS
                    .into_iter()
                    .chain(TITLE_KEYWORD_FIELDS)
                {
                    editor.entry(key).or_insert(json!(false));
                }
                for key in ["titleKeyword", "titleKeywordColor", "subtitleKeywordColor"] {
                    editor.entry(key).or_insert(json!(""));
                }
                for key in ["titleKeywordSize", "subtitleKeywordSize"] {
                    editor.entry(key).or_insert(json!(0));
                }
                for key in ["titleFont", "subtitleFont"] {
                    editor.entry(key).or_insert(json!("Alibaba PuHuiTi"));
                }
            }
        }
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
    require_complete(&validated)?;
    if record["effects"] != validated["effects"] || record["effect_ids"] != validated["effect_ids"]
    {
        return Err("模板效果快照与配置不一致".into());
    }
    Ok(record)
}

/// 每条记录独立生成摘要，无效配置保留标识及原因，不修改原文件。
fn summarize(record: &Value) -> Value {
    let mut summary = json!({
        "template_id": record["template_id"], "name": record["name"],
        "created_at": record["created_at"], "updated_at": record["updated_at"],
        "description": "", "title": "", "subtitle": "", "validation_error": "",
    });
    match validate_record(record) {
        Err(error) => summary["validation_error"] = json!(error),
        Ok(record) => {
            summary["description"] = record["description"].clone();
            let tracks = record["tracks"].as_array().expect("已经校验轨道数组");
            summary["track_count"] = json!(tracks.len());
            for track in tracks {
                if track["language"] == "en" {
                    continue;
                }
                if let Some(target @ ("title" | "subtitle")) = track["target"].as_str() {
                    summary[target] = track["editor"][target].clone();
                }
            }
        }
    }
    summary
}

/// 所有操作在同一文件锁下执行；临时文件刷盘后替换，失败保留原文件且允许重试。
fn operate(
    path: &Path,
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
    let directory = path.parent().ok_or("本地模板路径缺少目录")?;
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
    let mut records = read(path)?;
    let index = id.and_then(|id| records.iter().position(|item| item["template_id"] == id));
    if id.is_some() && index.is_none() {
        return Err("模板不存在".into());
    }
    let result = match operation {
        "list" => return Ok(json!(records.iter().map(summarize).collect::<Vec<_>>())),
        "get" => {
            let record = &records[index.ok_or("模板不存在")?];
            return validate_record(record).map_err(|error| {
                format!("模板 {} 不符合当前规则：{error}", id.expect("已校验 ID"))
            });
        }
        "delete" => {
            records.remove(index.ok_or("模板不存在")?);
            Value::Null
        }
        "save" => {
            let mut saved = validate(draft.ok_or("缺少模板配置")?)?;
            // 创建与更新均要求两个固定文字对象，校验失败保留已有文件。
            require_complete(&saved)?;
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
    // 同目录唯一临时文件独占创建，不跟随或截断用户已有文件；失败只清理本次创建的文件。
    let bytes = serde_json::to_vec_pretty(&records).map_err(|error| error.to_string())?;
    let temporary = path.with_extension(format!("json.{}.tmp", Uuid::new_v4()));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("保存本地模板失败，原数据已保留：{error}"))?;
    let written = file.write_all(&bytes).and_then(|_| file.sync_all());
    drop(file);
    if let Err(error) = written.and_then(|_| fs::rename(&temporary, path)) {
        let _ = fs::remove_file(&temporary);
        return Err(format!("保存本地模板失败，原数据已保留：{error}"));
    }
    Ok(result)
}

/// 读取通用设置的 template_path；未设置使用默认文件，自定义须为 .json 绝对路径。
/// 设置文件原子替换写入，读取无需加锁；文件损坏时明确报错，不回退其他库。
fn storage_path(app_data: &Path) -> Result<PathBuf, String> {
    let settings = match fs::read(app_data.join("data/settings/settings.json")) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|_| "本地设置文件损坏")?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => json!({}),
        Err(error) => return Err(format!("读取本地设置失败：{error}")),
    };
    let value = match &settings["$client"]["template_path"] {
        Value::Null => "",
        value => value.as_str().ok_or("本地模板保存路径须为字符串")?,
    }
    .trim();
    if value.is_empty() {
        return Ok(app_data.join("data/template/templates.json"));
    }
    let path = PathBuf::from(value);
    if !path.is_absolute()
        || !path
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
    {
        return Err("本地模板保存路径须为以 .json 结尾的绝对文件路径".into());
    }
    Ok(path)
}

/// 按当前设置定位模板文件后执行；编辑器传回打开时所属的库，库已变化时拒绝写入。
fn run(
    app_data: &Path,
    operation: &str,
    id: Option<&str>,
    draft: Option<Value>,
    library: Option<&str>,
) -> Result<Value, String> {
    let path = storage_path(app_data)?;
    let current = path.to_string_lossy();
    if library.is_some_and(|library| library != current) {
        return Err(
            "本地模板保存路径已变更，请回到主页重新打开模板；当前修改仍保留在编辑器中".into(),
        );
    }
    let mut result = operate(&path, operation, id, draft)?;
    // 单条记录附带所属库供编辑器保存时校验，不写入模板文件。
    if let Some(record) = result.as_object_mut() {
        record.insert("library".into(), json!(current));
    }
    if let Some(records) = result.as_array_mut() {
        for record in records {
            record["library"] = json!(current);
        }
    }
    Ok(result)
}

/// 每次操作按当前设置定位模板文件；不调用 Python 服务。
#[tauri::command]
pub fn local_templates(
    app: tauri::AppHandle,
    operation: String,
    id: Option<String>,
    draft: Option<Vec<u8>>,
    library: Option<String>,
) -> Result<Value, String> {
    let draft = draft
        .map(|bytes| draft_from_protobuf(&bytes, id.as_deref()))
        .transpose()?;
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    run(
        &app_data,
        &operation,
        id.as_deref(),
        draft,
        library.as_deref(),
    )
}

#[cfg(test)]
mod tests {
    //! 使用临时目录验证离线增删改查、校验、锁冲突和失败保留；cargo test --locked。
    use super::*;

    /// 既有用例按目录调用默认文件名，实际读写与自定义路径共用实现。
    fn operate(
        directory: &Path,
        operation: &str,
        id: Option<&str>,
        draft: Option<Value>,
    ) -> Result<Value, String> {
        super::operate(&directory.join("templates.json"), operation, id, draft)
    }

    /// 未设置用默认文件；自定义路径（扩展名大小写均可）独立读写；库变化后拒绝旧编辑器写入；非法路径与损坏设置报错。
    #[test]
    fn storage_path_follows_client_setting() {
        let dir = Directory::new();
        let settings = dir.0.join("data/settings");
        fs::create_dir_all(&settings).unwrap();
        let default = dir.0.join("data/template/templates.json");
        assert_eq!(storage_path(&dir.0).unwrap(), default);
        let custom = dir.0.join("custom/TEMPLATES.JSON");
        let write = |value: Value| {
            fs::write(
                settings.join("settings.json"),
                serde_json::to_vec(&json!({"$client": {"template_path": value}})).unwrap(),
            )
            .unwrap()
        };
        write(json!(custom));
        assert_eq!(storage_path(&dir.0).unwrap(), custom);
        let saved = run(&dir.0, "save", None, Some(draft("自定义")), None).unwrap();
        let library = saved["library"].as_str().unwrap().to_owned();
        assert_eq!(library, custom.to_string_lossy());
        assert!(!default.exists());
        // 备份保留相同 ID；切到备份后，按原库打开的编辑器不能覆盖备份。
        let backup = dir.0.join("backup.json");
        fs::copy(&custom, &backup).unwrap();
        write(json!(backup));
        let id = saved["template_id"].as_str();
        assert!(
            run(&dir.0, "save", id, Some(draft("旧库草稿")), Some(&library))
                .unwrap_err()
                .contains("路径已变更")
        );
        assert_eq!(fs::read(&backup).unwrap(), fs::read(&custom).unwrap());
        let reopened = run(&dir.0, "get", id, None, None).unwrap();
        let backup_library = reopened["library"].as_str().unwrap();
        run(
            &dir.0,
            "save",
            id,
            Some(draft("备份")),
            Some(backup_library),
        )
        .unwrap();
        assert_eq!(
            super::operate(&custom, "get", id, None).unwrap()["name"],
            "自定义"
        );
        write(json!(""));
        assert_eq!(storage_path(&dir.0).unwrap(), default);
        write(json!(1));
        assert!(storage_path(&dir.0).is_err());
        write(json!("relative.json"));
        assert!(storage_path(&dir.0).is_err());
        write(json!(dir.0.join("templates.txt")));
        assert!(storage_path(&dir.0).is_err());
        fs::write(settings.join("settings.json"), b"broken").unwrap();
        assert!(storage_path(&dir.0).unwrap_err().contains("损坏"));
    }

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
        let mut editor = json!({"title": "标题", "subtitle": "", "bubbleText": "", "titleFlower": "", "subtitleFlower": "", "bubble": "", "filter": "", "vfx": "", "transition": ""});
        for key in SUBTITLE_KEYWORD_FIELDS {
            editor[key] = json!(false);
        }
        editor["subtitleKeywordColor"] = json!("");
        editor["subtitleKeywordSize"] = json!(0);
        for key in TITLE_KEYWORD_FIELDS {
            editor[key] = json!(false);
        }
        editor["titleKeyword"] = json!("");
        editor["titleKeywordColor"] = json!("");
        editor["titleKeywordSize"] = json!(0);
        editor["titleFont"] = json!("Alibaba PuHuiTi");
        editor["subtitleFont"] = json!("Alibaba PuHuiTi");
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
        let mut subtitle = editor.clone();
        subtitle["title"] = json!("");
        subtitle["titleIn"] = json!("");
        subtitle["subtitle"] = json!("字幕");
        json!({"name": name, "description": "  说明  ", "tracks": [
            {"id": "title", "target": "title", "start_mode": "seconds", "start": 0, "duration": null, "editor": editor},
            {"id": "subtitle", "target": "subtitle", "start_mode": "seconds", "start": 0, "duration": null, "editor": subtitle}
        ], "transition_duration_seconds": 0.5})
    }

    /// 构造带完整编辑字段的合法 Protobuf 请求，供保存与拒绝路径共同使用。
    fn protobuf_request() -> generated::SaveTemplateRequest {
        let editor = generated::EffectTemplateEditor {
            title: Some("标题".into()),
            subtitle: Some("".into()),
            bubble_text: Some("".into()),
            title_size: Some(40),
            subtitle_size: Some(40),
            bubble_size: Some(40),
            title_x: Some(50.),
            title_y: Some(50.),
            subtitle_x: Some(50.),
            subtitle_y: Some(50.),
            bubble_x: Some(50.),
            bubble_y: Some(50.),
            title_flower: Some("".into()),
            subtitle_flower: Some("".into()),
            bubble: Some("".into()),
            filter: Some("".into()),
            vfx: Some("".into()),
            transition: Some("".into()),
            title_in: Some("in/fade_in".into()),
            title_out: Some("".into()),
            title_loop: Some("".into()),
            subtitle_in: Some("".into()),
            subtitle_out: Some("".into()),
            subtitle_loop: Some("".into()),
            bubble_in: Some("".into()),
            bubble_out: Some("".into()),
            bubble_loop: Some("".into()),
            title_in_duration: Some(0.5),
            title_out_duration: Some(0.5),
            subtitle_in_duration: Some(0.5),
            subtitle_out_duration: Some(0.5),
            bubble_in_duration: Some(0.5),
            bubble_out_duration: Some(0.5),
            subtitle_keyword_bold: false,
            subtitle_keyword_italic: false,
            subtitle_keyword_underline: false,
            subtitle_keyword_strikeout: false,
            subtitle_keyword_color: "".into(),
            subtitle_keyword_size: 0,
            title_keyword: "".into(),
            title_keyword_bold: false,
            title_keyword_italic: false,
            title_keyword_underline: false,
            title_keyword_strikeout: false,
            title_keyword_color: "".into(),
            title_keyword_size: 0,
            remotion: None,
            title_font: None,
            subtitle_font: None,
        };
        let mut subtitle = editor.clone();
        subtitle.title = Some(String::new());
        subtitle.title_in = Some(String::new());
        subtitle.subtitle = Some("字幕".into());
        generated::SaveTemplateRequest {
            name: "  模板  ".into(),
            description: Some("  说明  ".into()),
            effect_ids: vec!["in/fade_in".into()],
            transition_duration_seconds: Some(0.5),
            transition_mode: None,
            transition_enabled: None,
            tracks: Some(generated::TrackList {
                tracks: vec![
                    generated::EffectTrack {
                        id: "title".into(),
                        target: "title".into(),
                        start_mode: "seconds".into(),
                        start: Some(0.),
                        duration: None,
                        editor: Some(editor),
                        sprite_id: String::new(),
                        transition_boundary: None,
                        text_target: None,
                        language: None,
                        enabled: None,
                        in_place: None,
                    },
                    generated::EffectTrack {
                        id: "subtitle".into(),
                        target: "subtitle".into(),
                        start_mode: "seconds".into(),
                        start: Some(0.),
                        editor: Some(subtitle),
                        ..Default::default()
                    },
                ],
            }),
            template_id: None,
        }
    }

    #[test]
    /// 多个转场和统一设置通过 Protobuf 与真实文件保存，重复序号不写入。
    fn transition_boundaries_survive_protobuf_and_file() {
        let dir = Directory::new();
        let mut request = protobuf_request();
        request.transition_mode = Some("random".into());
        request.transition_enabled = Some(false);
        request.transition_duration_seconds = Some(1.);
        let mut track = request.tracks.as_ref().unwrap().tracks[0].clone();
        let text_tracks = request.tracks.as_ref().unwrap().tracks.clone();
        track.id = "transition-1".into();
        track.target = "transition".into();
        track.start = None;
        track.start_mode = String::new();
        track.duration = Some(1.);
        track.transition_boundary = Some(1);
        let editor = track.editor.as_mut().unwrap();
        editor.title = Some(String::new());
        editor.title_in = Some(String::new());
        editor.transition = Some("transition/normal/directional".into());
        let mut second = track.clone();
        second.id = "transition-2".into();
        second.transition_boundary = Some(2);
        let mut third = track.clone();
        third.id = "transition-3".into();
        third.transition_boundary = Some(3);
        request.effect_ids = vec!["transition/normal/directional".into(), "in/fade_in".into()];
        request.tracks.as_mut().unwrap().tracks = vec![track, second, third];
        request.tracks.as_mut().unwrap().tracks.extend(text_tracks);
        let value = draft_from_protobuf(&request.encode_to_vec(), None).unwrap();
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        assert_eq!(saved["tracks"][0]["transition_boundary"], json!(1));
        assert_eq!(saved["tracks"][1]["transition_boundary"], json!(2));
        assert_eq!(saved["tracks"][2]["transition_boundary"], json!(3));
        assert_eq!(saved["transition_mode"], json!("random"));
        assert_eq!(saved["transition_enabled"], json!(false));
        assert!(saved["tracks"][0].get("start").is_none());
        let mut missing = value.clone();
        missing["tracks"][0]
            .as_object_mut()
            .unwrap()
            .remove("transition_boundary");
        assert!(operate(&dir.0, "save", None, Some(missing)).is_err());
        let mut positioned = value.clone();
        positioned["tracks"][0]["start"] = json!(9);
        assert!(operate(&dir.0, "save", None, Some(positioned)).is_err());
        for boundary in [json!(0), json!(1), json!(101), json!(true)] {
            let mut invalid = value.clone();
            invalid["tracks"][1]["transition_boundary"] = boundary;
            assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
            assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        }
    }

    #[test]
    /// 标题和字幕仅允许一个，字幕必须覆盖全程，无效更新保留真实文件。
    fn singleton_and_subtitle_time_validation() {
        let dir = Directory::new();
        for target in ["title", "subtitle"] {
            let mut value = draft(target);
            if target == "subtitle" {
                value["tracks"].as_array_mut().unwrap().swap(0, 1);
            }
            let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
            let id = saved["template_id"].as_str().unwrap();
            let mut duplicate = value["tracks"][0].clone();
            duplicate["id"] = json!("duplicate");
            let mut invalid = value.clone();
            invalid["tracks"].as_array_mut().unwrap().push(duplicate);
            assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
            if target == "subtitle" {
                for (field, input) in [
                    ("start", json!(1)),
                    ("duration", json!(10)),
                    ("start_mode", json!("percent")),
                ] {
                    let mut invalid = value.clone();
                    invalid["tracks"][0][field] = input;
                    assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
                }
            }
            assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        }
    }

    #[test]
    /// 缺少标题、字幕或两者时拒绝创建与更新，已有文件内容逐字节保持不变。
    fn save_requires_both_text_tracks() {
        let dir = Directory::new();
        let value = draft("固定文字");
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        let path = dir.0.join("templates.json");
        let original = fs::read(&path).unwrap();
        for missing in [vec!["title"], vec!["subtitle"], vec!["title", "subtitle"]] {
            let mut invalid = value.clone();
            invalid["tracks"]
                .as_array_mut()
                .unwrap()
                .retain(|track| !missing.contains(&track["target"].as_str().unwrap()));
            for existing in [None, Some(id)] {
                assert!(operate(&dir.0, "save", existing, Some(invalid.clone())).is_err());
                assert_eq!(fs::read(&path).unwrap(), original);
                assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
            }
        }
    }

    #[test]
    /// 停用英文仍通过 Protobuf 和文件保存，摘要取中文，重新开启恢复样式；错误时间不写入。
    fn bilingual_roundtrip_keeps_disabled_settings() {
        let dir = Directory::new();
        let mut request = protobuf_request();
        let mut english = request.tracks.as_ref().unwrap().tracks[0].clone();
        english.id = "title-en".into();
        english.language = Some("en".into());
        english.enabled = Some(false);
        english.editor.as_mut().unwrap().title = Some("English sample".into());
        english.editor.as_mut().unwrap().title_size = Some(28);
        request.tracks.as_mut().unwrap().tracks.push(english);
        let value = draft_from_protobuf(&request.encode_to_vec(), None).unwrap();
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        assert_eq!(saved["tracks"][2]["enabled"], false);
        assert_eq!(saved["tracks"][2]["editor"]["titleSize"], 28);
        assert_eq!(
            summarize(&saved)["title"],
            saved["tracks"][0]["editor"]["title"]
        );
        let mut enabled = value.clone();
        enabled["tracks"][2]["enabled"] = json!(true);
        let restored = operate(&dir.0, "save", Some(id), Some(enabled)).unwrap();
        assert_eq!(
            restored["tracks"][2]["editor"],
            saved["tracks"][2]["editor"]
        );
        let mut invalid = value;
        invalid["tracks"][2]["start"] = json!(1.0);
        assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), restored);
    }

    #[test]
    /// 关键词对象沿用所属字幕的样式字段，可以停用并保留设置；时间不跟随、带示例文字或句内关键词样式、重复都拒绝写入。
    fn keyword_object_roundtrip_and_validation() {
        let dir = Directory::new();
        let mut value = draft("关键词");
        let mut keyword = value["tracks"][1].clone();
        keyword["id"] = json!("subtitle-keyword");
        keyword["target"] = json!("subtitle_keyword");
        keyword["enabled"] = json!(false);
        keyword["editor"]["subtitle"] = json!("");
        keyword["editor"]["subtitleSize"] = json!(64.);
        keyword["editor"]["subtitleIn"] = json!("in/fade_in");
        value["tracks"].as_array_mut().unwrap().push(keyword);
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        assert_eq!(saved["tracks"][2]["enabled"], false);
        assert_eq!(saved["tracks"][2]["editor"]["subtitleSize"], 64.);
        assert_eq!(summarize(&saved)["subtitle"], "字幕");
        let mutations: [fn(&mut Value); 4] = [
            |draft| draft["tracks"][2]["start"] = json!(1.0),
            |draft| draft["tracks"][2]["editor"]["subtitle"] = json!("示例"),
            |draft| draft["tracks"][2]["editor"]["subtitleKeywordBold"] = json!(true),
            |draft| {
                let mut copy = draft["tracks"][2].clone();
                copy["id"] = json!("duplicate");
                draft["tracks"].as_array_mut().unwrap().push(copy);
            },
        ];
        for mutate in mutations {
            let mut invalid = value.clone();
            mutate(&mut invalid);
            assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
        }
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
    }

    #[test]
    /// 真实 Protobuf 保存消息转换后沿用 JSON 文件结构，缺失字段和错误 ID 均拒绝。
    fn protobuf_save_keeps_json_file_format() {
        let mut request = protobuf_request();
        request.tracks.as_mut().unwrap().tracks[0]
            .editor
            .as_mut()
            .unwrap()
            .title_keyword_size = 72;
        let bytes = request.encode_to_vec();
        let converted = draft_from_protobuf(&bytes, None).unwrap();
        let mut expected = draft("  模板  ");
        expected["tracks"][0]["editor"]["titleKeywordSize"] = json!(72);
        for track in expected["tracks"].as_array_mut().unwrap() {
            track["start"] = json!(0.0);
            for role in ["title", "subtitle", "bubble"] {
                track["editor"][format!("{role}Size")] = json!(40);
            }
        }
        assert_eq!(converted, expected);
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(converted)).unwrap();
        let records: Value =
            serde_json::from_slice(&fs::read(dir.0.join("templates.json")).unwrap()).unwrap();
        assert_eq!(records, json!([saved]));
        assert!(records[0]["tracks"].is_array());
        let mut missing_tracks = request;
        missing_tracks.tracks = None;
        assert_eq!(
            draft_from_protobuf(&missing_tracks.encode_to_vec(), None).unwrap_err(),
            "轨道须为数组"
        );
        assert!(draft_from_protobuf(&bytes, Some("different-id")).is_err());
        assert!(draft_from_protobuf(&[0xff], None).is_err());
    }

    #[test]
    /// 十一种字体经过 Protobuf 和真实文件保存后保留，旧文件读取补齐默认值且不改写原文件。
    fn fonts_roundtrip_and_restore_missing_fields() {
        let dir = Directory::new();
        let fonts: Vec<Value> = serde_json::from_str(include_str!(
            "../../../server/src/server/template/fonts.json"
        ))
        .unwrap();
        assert_eq!(fonts.len(), 11);
        for font in fonts {
            let mut request = protobuf_request();
            request.name = font["label"].as_str().unwrap().to_owned();
            let tracks = &mut request.tracks.as_mut().unwrap().tracks;
            tracks[0].editor.as_mut().unwrap().title_font =
                Some(font["value"].as_str().unwrap().into());
            tracks[1].editor.as_mut().unwrap().subtitle_font = Some("Source Han Serif CN".into());
            let converted = draft_from_protobuf(&request.encode_to_vec(), None).unwrap();
            let saved = operate(&dir.0, "save", None, Some(converted)).unwrap();
            let id = saved["template_id"].as_str().unwrap();
            let loaded = operate(&dir.0, "get", Some(id), None).unwrap();
            assert_eq!(loaded["tracks"][0]["editor"]["titleFont"], font["value"]);
            assert_eq!(
                loaded["tracks"][1]["editor"]["subtitleFont"],
                "Source Han Serif CN"
            );
        }
        let path = dir.0.join("templates.json");
        let mut records: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        for track in records[0]["tracks"].as_array_mut().unwrap() {
            track["editor"].as_object_mut().unwrap().remove("titleFont");
            track["editor"]
                .as_object_mut()
                .unwrap()
                .remove("subtitleFont");
        }
        fs::write(&path, serde_json::to_vec(&records).unwrap()).unwrap();
        let before = fs::read(&path).unwrap();
        let loaded = operate(&dir.0, "get", records[0]["template_id"].as_str(), None).unwrap();
        assert_eq!(
            loaded["tracks"][0]["editor"]["titleFont"],
            "Alibaba PuHuiTi"
        );
        assert_eq!(
            loaded["tracks"][1]["editor"]["subtitleFont"],
            "Alibaba PuHuiTi"
        );
        assert_eq!(fs::read(&path).unwrap(), before);
    }

    #[test]
    /// 两个字体字段拒绝未知字体、空字符串、预览标识和非字符串，已有记录保持不变。
    fn unsupported_fonts_do_not_change_local_templates() {
        let dir = Directory::new();
        let valid = draft("字体校验");
        let saved = operate(&dir.0, "save", None, Some(valid.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        let path = dir.0.join("templates.json");
        let before = fs::read(&path).unwrap();
        for (index, field) in [(0, "titleFont"), (1, "subtitleFont")] {
            for font in [
                json!(""),
                json!("Arial"),
                json!("思源宋体"),
                json!("Source Han Sans CN Normal"),
                json!(42),
            ] {
                let mut invalid = valid.clone();
                invalid["tracks"][index]["editor"][field] = font;
                assert!(operate(&dir.0, "save", Some(id), Some(invalid)).is_err());
                assert_eq!(fs::read(&path).unwrap(), before);
            }
        }
    }

    #[test]
    /// 缺失、重复、未知或不匹配的效果列表拒绝更新，保留原模板文件。
    fn protobuf_rejects_invalid_effect_ids_without_writes() {
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(draft("保留"))).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        let path = dir.0.join("templates.json");
        let original = fs::read(&path).unwrap();
        for effect_ids in [
            vec![],
            vec!["in/fade_in", "in/fade_in"],
            vec!["in/unknown"],
            vec!["in/blur_in"],
            vec!["in/fade_in", "in/blur_in"],
        ] {
            let mut request = protobuf_request();
            request.template_id = Some(id.into());
            request.effect_ids = effect_ids.iter().map(|value| (*value).into()).collect();
            let result = draft_from_protobuf(&request.encode_to_vec(), Some(id))
                .and_then(|draft| operate(&dir.0, "save", Some(id), Some(draft)));
            assert_eq!(result.unwrap_err(), "所选特效与编辑配置不一致");
            assert_eq!(fs::read(&path).unwrap(), original);
        }
    }

    #[test]
    /// Remotion 片段只存在于云端模板：remotion 轨道、任何轨道上的 Sprite 引用、Remotion 参数覆盖或字效作用的文字对象都被明确拒绝，模板文件保持原样。
    fn protobuf_refuses_remotion_tracks_without_writes() {
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(draft("保留"))).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        let path = dir.0.join("templates.json");
        let original = fs::read(&path).unwrap();
        for (target, sprite_id) in [
            ("remotion", "6f1c8a52-3d94-4b7e-9a10-2c5e8d7b4f31"),
            ("title", "6f1c8a52-3d94-4b7e-9a10-2c5e8d7b4f31"),
            ("remotion", ""),
        ] {
            let mut request = protobuf_request();
            request.template_id = Some(id.into());
            let track = &mut request.tracks.as_mut().unwrap().tracks[0];
            track.target = target.into();
            track.sprite_id = sprite_id.into();
            let result = draft_from_protobuf(&request.encode_to_vec(), Some(id))
                .and_then(|draft| operate(&dir.0, "save", Some(id), Some(draft)));
            assert_eq!(result.unwrap_err(), "本地模板不支持 Remotion 资产");
            assert_eq!(fs::read(&path).unwrap(), original);
        }
        let mut request = protobuf_request();
        request.template_id = Some(id.into());
        request.tracks.as_mut().unwrap().tracks[0]
            .editor
            .as_mut()
            .unwrap()
            .remotion = Some(generated::RemotionTrackEditor::default());
        let result = draft_from_protobuf(&request.encode_to_vec(), Some(id))
            .and_then(|draft| operate(&dir.0, "save", Some(id), Some(draft)));
        assert_eq!(result.unwrap_err(), "本地模板不支持 Remotion 资产");
        assert_eq!(fs::read(&path).unwrap(), original);
        let mut request = protobuf_request();
        request.template_id = Some(id.into());
        request.tracks.as_mut().unwrap().tracks[0].text_target = Some("title".into());
        let result = draft_from_protobuf(&request.encode_to_vec(), Some(id))
            .and_then(|draft| operate(&dir.0, "save", Some(id), Some(draft)));
        assert_eq!(result.unwrap_err(), "本地模板不支持 Remotion 资产");
        assert_eq!(fs::read(&path).unwrap(), original);
        // 句中原位的关键词要用 Remotion 字效画在原句里，本地模板同样不支持。
        let mut request = protobuf_request();
        request.template_id = Some(id.into());
        request.tracks.as_mut().unwrap().tracks[0].in_place = Some(true);
        let result = draft_from_protobuf(&request.encode_to_vec(), Some(id))
            .and_then(|draft| operate(&dir.0, "save", Some(id), Some(draft)));
        assert_eq!(result.unwrap_err(), "本地模板不支持 Remotion 资产");
        assert_eq!(fs::read(&path).unwrap(), original);
    }

    #[test]
    /// 效果列表顺序可变，多个轨道共用效果只需声明一次，保存后仍可重新读取。
    fn protobuf_accepts_reordered_and_shared_effect_ids() {
        let mut request = protobuf_request();
        let tracks = &mut request.tracks.as_mut().unwrap().tracks;
        let title = tracks[0].clone();
        tracks[0].id = "bubble-a".into();
        tracks[0].target = "bubble".into();
        let editor = tracks[0].editor.as_mut().unwrap();
        editor.bubble_text = editor.title.take();
        editor.title = Some(String::new());
        editor.bubble_in = editor.title_in.take();
        editor.title_in = Some(String::new());
        let mut second = tracks[0].clone();
        second.id = "bubble-b".into();
        second.editor.as_mut().unwrap().bubble_in = Some("in/blur_in".into());
        let mut third = tracks[0].clone();
        third.id = "bubble-c".into();
        tracks.extend([second, third, title]);
        request.effect_ids = vec!["in/blur_in".into(), "in/fade_in".into()];

        let converted = draft_from_protobuf(&request.encode_to_vec(), None).unwrap();
        assert_eq!(converted.as_object().unwrap().len(), 4);
        assert!(converted.get("effect_ids").is_none());
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(converted)).unwrap();
        assert_eq!(saved["effect_ids"], json!(["in/fade_in", "in/blur_in"]));
        assert_eq!(saved["tracks"].as_array().unwrap().len(), 5);
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
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
        let listed = operate(&dir.0, "list", None, None).unwrap();
        assert_eq!(listed[0]["template_id"], copy["template_id"]);
        assert_eq!(listed[1]["name"], updated["name"]);
        assert_eq!(listed[0]["track_count"], 2);
        assert_eq!(listed[0]["validation_error"], "");
        assert!(operate(&dir.0, "save", Some(id), Some(draft("副本"))).is_err());
        operate(&dir.0, "delete", Some(id), None).unwrap();
        assert!(operate(&dir.0, "delete", Some(id), None).is_err());
        assert!(operate(&dir.0, "get", Some(id), None).is_err());
        assert!(operate(&dir.0, "save", Some(id), Some(draft("不能重建"))).is_err());
        let listed = operate(&dir.0, "list", None, None).unwrap();
        assert_eq!(listed.as_array().unwrap().len(), 1);
        assert_eq!(listed[0]["template_id"], copy["template_id"]);
        assert!(fs::read_dir(&dir.0).unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));
    }

    #[test]
    /// 底部字幕样式经本地文件保存和重新读取保持不变，旧记录在内存中补充关闭值。
    fn subtitle_keyword_style_survives_reload_and_old_records() {
        let dir = Directory::new();
        let mut styled = draft("关键词模板");
        styled["tracks"].as_array_mut().unwrap().swap(0, 1);
        styled["tracks"][0]["editor"]["title"] = json!("");
        styled["tracks"][0]["editor"]["subtitle"] = json!("示例字幕");
        styled["tracks"][0]["editor"]["titleIn"] = json!("");
        styled["tracks"][0]["editor"]["subtitleIn"] = json!("in/fade_in");
        styled["tracks"][0]["editor"]["subtitleKeywordBold"] = json!(true);
        styled["tracks"][0]["editor"]["subtitleKeywordUnderline"] = json!(true);
        styled["tracks"][0]["editor"]["subtitleKeywordColor"] = json!("#12AB34");
        styled["tracks"][0]["editor"]["subtitleKeywordSize"] = json!(64);
        let saved = operate(&dir.0, "save", None, Some(styled)).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);

        let mut old_records = json!([saved]);
        for key in SUBTITLE_KEYWORD_FIELDS {
            old_records[0]["tracks"][0]["editor"]
                .as_object_mut()
                .unwrap()
                .remove(key);
        }
        old_records[0]["tracks"][0]["editor"]
            .as_object_mut()
            .unwrap()
            .remove("subtitleKeywordColor");
        old_records[0]["tracks"][0]["editor"]
            .as_object_mut()
            .unwrap()
            .remove("subtitleKeywordSize");
        for key in TITLE_KEYWORD_FIELDS {
            old_records[0]["tracks"][0]["editor"]
                .as_object_mut()
                .unwrap()
                .remove(key);
        }
        for key in ["titleKeyword", "titleKeywordColor", "titleKeywordSize"] {
            old_records[0]["tracks"][0]["editor"]
                .as_object_mut()
                .unwrap()
                .remove(key);
        }
        let path = dir.0.join("templates.json");
        let original = serde_json::to_vec(&old_records).unwrap();
        fs::write(&path, &original).unwrap();
        let restored = operate(&dir.0, "get", Some(id), None).unwrap();
        assert_eq!(
            restored["tracks"][0]["editor"]["subtitleKeywordBold"],
            false
        );
        assert_eq!(
            restored["tracks"][0]["editor"]["subtitleKeywordUnderline"],
            false
        );
        assert_eq!(restored["tracks"][0]["editor"]["subtitleKeywordColor"], "");
        assert_eq!(restored["tracks"][0]["editor"]["subtitleKeywordSize"], 0);
        assert_eq!(restored["tracks"][0]["editor"]["titleKeyword"], "");
        assert_eq!(restored["tracks"][0]["editor"]["titleKeywordBold"], false);
        assert_eq!(fs::read(path).unwrap(), original);
    }

    #[test]
    /// 标题关键词样式经本地保存和重新读取保持不变。
    fn title_keyword_style_survives_reload() {
        let dir = Directory::new();
        let mut styled = draft("标题关键词模板");
        styled["tracks"][0]["editor"]["titleKeyword"] = json!("标题");
        styled["tracks"][0]["editor"]["titleKeywordBold"] = json!(true);
        styled["tracks"][0]["editor"]["titleKeywordColor"] = json!("#12AB34");
        styled["tracks"][0]["editor"]["titleKeywordSize"] = json!(72);
        let saved = operate(&dir.0, "save", None, Some(styled)).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
    }

    #[test]
    /// 无效配置和任意路径 ID 被拒绝，保留既有数据；有效数值边界允许保存。
    fn validates_input_without_writes() {
        let dir = Directory::new();
        let saved = operate(&dir.0, "save", None, Some(draft("保留"))).unwrap();
        for (pointer, value) in [
            ("/name", json!(" ")),
            ("/tracks/0/editor/titleSize", json!(12.5)),
            ("/tracks/0/editor/titleX", json!(101)),
            ("/tracks/0/editor/titleInDuration", Value::Null),
            ("/tracks/0/editor/titleIn", json!("in/unknown")),
            ("/tracks/0/editor/titleLoop", json!("loop/bounce")),
            ("/tracks/0/editor/titleFlower", json!("filter/fake")),
            ("/tracks/0/editor/subtitleKeywordBold", json!(true)),
            ("/tracks/0/editor/subtitleKeywordItalic", json!("true")),
            ("/tracks/0/editor/subtitleKeywordColor", json!("#12AB34")),
            ("/tracks/0/editor/subtitleKeywordColor", json!("orange")),
            ("/tracks/0/editor/titleKeywordColor", json!("orange")),
            ("/tracks/0/editor/titleKeywordSize", json!(11)),
            ("/tracks/0/editor/subtitleKeywordSize", json!(64)),
            ("/tracks/0/editor/titleKeyword", json!("题".repeat(61))),
            ("/transition_duration_seconds", json!(0)),
            ("/tracks/0/editor/title", json!("题".repeat(61))),
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
        let listed = operate(&dir.0, "list", None, None).unwrap();
        assert_eq!(listed.as_array().unwrap().len(), 1);
        assert_eq!(listed[0]["template_id"], saved["template_id"]);
        assert_eq!(listed[0]["validation_error"], "");
        let mut boundary = draft("边界");
        boundary["tracks"][0]["editor"]["titleSize"] = json!(12);
        boundary["tracks"][0]["editor"]["titleX"] = json!(100);
        boundary["transition_duration_seconds"] = json!(3);
        assert!(operate(&dir.0, "save", None, Some(boundary)).is_ok());
    }

    #[test]
    /// 时间规则从真实文件恢复，秒数不受预览限制，非法百分比或持续时间保留原记录。
    fn multiple_tracks_survive_reload() {
        let dir = Directory::new();
        let mut value = draft("多轨模板");
        let mut editor = value["tracks"][0]["editor"].clone();
        editor["bubbleText"] = editor["title"].clone();
        editor["title"] = json!("");
        editor["bubbleIn"] = editor["titleIn"].clone();
        editor["titleIn"] = json!("");
        value["tracks"] = json!([
            {"id": "bubble-a", "target": "bubble", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor},
            {"id": "bubble-b", "target": "bubble", "start_mode": "seconds", "start": 200, "duration": null, "editor": editor},
            value["tracks"][0], value["tracks"][1]
        ]);
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        assert_eq!(saved["tracks"], value["tracks"]);
        assert!(saved.get("media").is_none());
        assert!(saved.get("editor").is_none());
        assert_eq!(saved["effect_ids"], json!(["in/fade_in"]));
        for (field, invalid) in [
            ("duration", json!(0)),
            ("start", json!(100)),
            ("start_mode", json!("frames")),
            ("id", json!("bubble-b")),
            ("media", json!({})),
        ] {
            let mut rejected = value.clone();
            rejected["tracks"][0][field] = invalid;
            assert!(operate(&dir.0, "save", Some(id), Some(rejected)).is_err());
            assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        }
        value["tracks"].as_array_mut().unwrap().remove(0);
        let updated = operate(&dir.0, "save", Some(id), Some(value)).unwrap();
        assert_eq!(updated["tracks"].as_array().unwrap().len(), 3);
        assert_eq!(updated["tracks"][0]["id"], "bubble-b");
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), updated);
    }

    #[test]
    /// 无效记录出现在列表并报告原因，不能读取详情，可以按 ID 覆盖或删除。
    fn rejects_unsupported_template_format() {
        let dir = Directory::new();
        let value = draft("对象模板");
        let saved = operate(&dir.0, "save", None, Some(value.clone())).unwrap();
        let id = saved["template_id"].as_str().unwrap();
        let path = dir.0.join("templates.json");
        let original = fs::read(&path).unwrap();
        for kind in ["missing-tracks", "null-tracks", "top-editor"] {
            let mut rejected = value.clone();
            match kind {
                "missing-tracks" => {
                    rejected.as_object_mut().unwrap().remove("tracks");
                }
                "null-tracks" => rejected["tracks"] = Value::Null,
                _ => rejected["editor"] = value["tracks"][0]["editor"].clone(),
            }
            assert!(operate(&dir.0, "save", None, Some(rejected.clone())).is_err());
            assert!(operate(&dir.0, "save", Some(id), Some(rejected.clone())).is_err());
            assert_eq!(fs::read(&path).unwrap(), original);
            let mut invalid_record = saved.clone();
            invalid_record.as_object_mut().unwrap().remove("tracks");
            invalid_record
                .as_object_mut()
                .unwrap()
                .extend(rejected.as_object().unwrap().clone());
            let bytes = serde_json::to_vec(&json!([invalid_record])).unwrap();
            fs::write(&path, &bytes).unwrap();
            let listed = operate(&dir.0, "list", None, None).unwrap();
            assert_eq!(listed[0]["template_id"], id);
            assert!(!listed[0]["validation_error"].as_str().unwrap().is_empty());
            assert!(operate(&dir.0, "get", Some(id), None)
                .unwrap_err()
                .contains(id));
            assert_eq!(fs::read(&path).unwrap(), bytes);
            assert!(operate(&dir.0, "save", Some(id), Some(value.clone())).is_ok());
            fs::write(&path, &original).unwrap();
        }
        assert_eq!(operate(&dir.0, "get", Some(id), None).unwrap(), saved);
        operate(&dir.0, "delete", Some(id), None).unwrap();
        assert_eq!(operate(&dir.0, "list", None, None).unwrap(), json!([]));
    }

    #[test]
    /// 同库无效配置逐条报告，删除指定记录后有效模板仍可读取，列表不改写文件。
    fn invalid_records_can_be_deleted_independently() {
        let dir = Directory::new();
        let valid = operate(&dir.0, "save", None, Some(draft("有效模板"))).unwrap();
        let invalid = operate(&dir.0, "save", None, Some(draft("无效模板"))).unwrap();
        let mut damaged = invalid.clone();
        damaged["tracks"].as_array_mut().unwrap().remove(1);
        let path = dir.0.join("templates.json");
        let original = serde_json::to_vec(&json!([valid, damaged])).unwrap();
        fs::write(&path, &original).unwrap();
        let summaries = operate(&dir.0, "list", None, None).unwrap();
        assert_eq!(summaries.as_array().unwrap().len(), 2);
        assert_eq!(summaries[0]["validation_error"], "");
        assert!(!summaries[1]["validation_error"]
            .as_str()
            .unwrap()
            .is_empty());
        assert_eq!(fs::read(&path).unwrap(), original);
        let valid_id = valid["template_id"].as_str();
        let invalid_id = invalid["template_id"].as_str();
        assert_eq!(operate(&dir.0, "get", valid_id, None).unwrap(), valid);
        assert!(operate(&dir.0, "get", invalid_id, None).is_err());
        operate(&dir.0, "delete", invalid_id, None).unwrap();
        assert_eq!(operate(&dir.0, "get", valid_id, None).unwrap(), valid);
        assert_eq!(
            operate(&dir.0, "list", None, None).unwrap(),
            json!([summaries[0]])
        );
    }

    #[test]
    /// 文件锁竞争和损坏 JSON 均可见报错，不覆盖原始文件，解除后可重试；用户同名临时文件不被覆盖。
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
        assert_eq!(fs::read(&path).unwrap(), original);
        fs::write(dir.0.join("templates.json.tmp"), b"user backup").unwrap();
        operate(&dir.0, "save", None, Some(draft("临时文件"))).unwrap();
        assert_eq!(
            fs::read(dir.0.join("templates.json.tmp")).unwrap(),
            b"user backup"
        );
        let original = fs::read(&path).unwrap();
        fs::write(&path, b"broken json").unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("禁止覆盖"))).is_err());
        assert_eq!(fs::read(&path).unwrap(), b"broken json");
        fs::write(&path, original).unwrap();
        assert!(operate(&dir.0, "save", None, Some(draft("恢复"))).is_ok());
    }
}
