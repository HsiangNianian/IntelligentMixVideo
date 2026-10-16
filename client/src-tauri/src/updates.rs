//! 客户端更新：镜像仅传输，内置公钥验证清单；资源作为单个文件原子切换，整包安装委托官方插件。
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    borrow::Cow,
    collections::BTreeMap,
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, OnceLock, RwLock,
    },
    time::Duration,
};
use tauri::{
    utils::assets::{AssetKey, AssetsIter, CspHash},
    Assets, Emitter, Manager, Runtime,
};
use tauri_plugin_updater::UpdaterExt;

const NATIVE_ID: &str = env!("IMV_NATIVE_ID");
const TARGET: &str = env!("IMV_UPDATE_TARGET");
const PUBLIC_KEY: &str = match option_env!("IMV_UPDATE_PUBLIC_KEY") {
    Some(v) => v,
    None => "",
};
const fn configured(value: Option<&'static str>, fallback: &'static str) -> &'static str {
    match value {
        Some(v) if !v.is_empty() => v,
        _ => fallback,
    }
}
const REPOSITORY: &str = configured(
    option_env!("IMV_UPDATE_REPOSITORY"),
    "HsiangNianian/IntelligentMixVideo",
);
const MIRRORS: &str = configured(
    option_env!("IMV_UPDATE_MIRRORS"),
    "https://gh-proxy.org/,https://ghfast.top/,https://ghproxy.net/,https://gpp.hydroroll.team/,https://ghproxy.hydroroll.team/",
);
const MAX_HOT: usize = 64 * 1024 * 1024;
type Result<T> = std::result::Result<T, String>;
type Files = BTreeMap<String, Vec<u8>>;
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn enabled() -> bool {
    !tauri::is_dev() && option_env!("IMV_DEBUG") != Some("true") && !PUBLIC_KEY.is_empty()
}

#[derive(Clone, Deserialize, Serialize)]
struct Asset {
    name: String,
    size: usize,
    sha256: String,
    #[serde(default)]
    signature: String,
}
#[derive(Clone, Deserialize, Serialize)]
struct Manifest {
    format: u32,
    version: String,
    target: String,
    native_id: String,
    #[serde(default)]
    hot_validated: bool,
    hot: Asset,
    installers: BTreeMap<String, Asset>,
}
#[derive(Deserialize, Serialize)]
struct Envelope {
    payload: String,
    signature: String,
}
#[derive(Default, Clone, Deserialize, Serialize)]
struct DiskState {
    current: Option<String>,
    previous: Option<String>,
    pending: Option<String>,
    #[serde(default)]
    rejected: Vec<String>,
    resume: Option<Value>,
}
struct Loaded {
    version: String,
    files: Files,
}
#[derive(Default)]
pub struct Updates {
    root: OnceLock<PathBuf>,
    active: RwLock<Option<Loaded>>,
    disk: Mutex<DiskState>,
    instance: Mutex<Option<File>>,
    exclusive_held: AtomicBool,
    operation: tokio::sync::Mutex<()>,
    candidate: Mutex<Option<(Manifest, Vec<u8>)>>,
    staged: Mutex<Option<(Manifest, Vec<u8>, String)>>,
    notice: Mutex<String>,
}

/// 使用原有资源协议和 Windows localhost，不添加新的来源权限。
struct UpdateAssets<R: Runtime> {
    original: OnceLock<Box<dyn Assets<R>>>,
    state: Arc<Updates>,
}
impl<R: Runtime> Assets<R> for UpdateAssets<R> {
    fn setup(&self, app: &tauri::App<R>) {
        self.original.get().unwrap().setup(app);
        if let Err(e) = self.state.start(app) {
            *self.state.notice.lock().unwrap() = format!("更新存储不可用：{e}");
        }
    }
    fn get(&self, key: &AssetKey) -> Option<Cow<'_, [u8]>> {
        if let Some(active) = self.state.active.read().unwrap().as_ref() {
            // 新旧资源不混用；缺失资产交给原协议返回错误。
            return active.files.get(key.as_ref()).cloned().map(Cow::Owned);
        }
        self.original.get().unwrap().get(key)
    }
    fn iter(&self) -> Box<AssetsIter<'_>> {
        self.original.get().unwrap().iter()
    }
    fn csp_hashes(&self, key: &AssetKey) -> Box<dyn Iterator<Item = CspHash<'_>> + '_> {
        self.original.get().unwrap().csp_hashes(key)
    }
}
// Arc 的 trait 转发让 Context 与命令处理共享同一份资源。
struct SharedAssets<R: Runtime>(Arc<UpdateAssets<R>>);
impl<R: Runtime> Assets<R> for SharedAssets<R> {
    fn setup(&self, app: &tauri::App<R>) {
        self.0.setup(app);
    }
    fn get(&self, key: &AssetKey) -> Option<Cow<'_, [u8]>> {
        self.0.get(key)
    }
    fn iter(&self) -> Box<AssetsIter<'_>> {
        self.0.iter()
    }
    fn csp_hashes(&self, key: &AssetKey) -> Box<dyn Iterator<Item = CspHash<'_>> + '_> {
        self.0.csp_hashes(key)
    }
}
pub fn attach<R: Runtime>(context: &mut tauri::Context<R>) -> Arc<Updates> {
    context.config_mut().plugins.0.insert("updater".into(), json!({
        "pubkey": PUBLIC_KEY,
        "endpoints": [format!("https://github.com/{REPOSITORY}/releases/latest/download/update-{TARGET}.json")]
    }));
    let state = Arc::new(Updates::default());
    let assets = Arc::new(UpdateAssets {
        original: OnceLock::new(),
        state: state.clone(),
    });
    let original = context.set_assets(Box::new(SharedAssets(assets.clone())));
    assert!(assets.original.set(original).is_ok());
    state
}

pub fn check_startup(app: &tauri::App) -> Result<()> {
    let state = app.state::<Arc<Updates>>();
    if enabled() && state.root.get().is_none() {
        return Err(state.notice.lock().unwrap().clone());
    }
    Ok(())
}

fn verify(bytes: &[u8], signature: &str, public_key: &str) -> Result<()> {
    let key = String::from_utf8(B64.decode(public_key.trim()).map_err(error)?).map_err(error)?;
    let sig = String::from_utf8(B64.decode(signature.trim()).map_err(error)?).map_err(error)?;
    minisign_verify::PublicKey::decode(&key)
        .map_err(error)?
        .verify(
            bytes,
            &minisign_verify::Signature::decode(&sig).map_err(error)?,
            true,
        )
        .map_err(error)
}
fn version(value: &str) -> Result<Version> {
    let parsed = Version::parse(value).map_err(error)?;
    if !parsed.pre.is_empty() || !parsed.build.is_empty() {
        return Err("仅支持正式版本".into());
    }
    Ok(parsed)
}
fn safe_name(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
        && value != "."
        && value != ".."
}
fn manifest(bytes: &[u8], public_key: &str) -> Result<Manifest> {
    let envelope: Envelope = serde_json::from_slice(bytes).map_err(error)?;
    let payload = B64.decode(envelope.payload).map_err(error)?;
    verify(&payload, &envelope.signature, public_key)?;
    let item: Manifest = serde_json::from_slice(&payload).map_err(error)?;
    version(&item.version)?;
    if item.format != 1 || item.target != TARGET {
        return Err("更新清单协议或平台不匹配".into());
    }
    for asset in std::iter::once(&item.hot).chain(item.installers.values()) {
        if !safe_name(&asset.name)
            || asset.size == 0
            || asset.size > 1024 * 1024 * 1024
            || asset.sha256.len() != 64
        {
            return Err("更新资产声明无效".into());
        }
    }
    Ok(item)
}
fn check_asset(asset: &Asset, bytes: &[u8]) -> Result<()> {
    if bytes.len() != asset.size || format!("{:x}", Sha256::digest(bytes)) != asset.sha256 {
        return Err("更新文件大小或摘要不匹配".into());
    }
    Ok(())
}
fn hot_files(asset: &Asset, bytes: &[u8]) -> Result<Files> {
    if bytes.len() > MAX_HOT {
        return Err("界面更新包过大".into());
    }
    check_asset(asset, bytes)?;
    let encoded: BTreeMap<String, String> = serde_json::from_slice(bytes).map_err(error)?;
    if encoded.len() > 4096
        || !encoded.contains_key("/index.html")
        || !encoded.contains_key("/template-preview.html")
    {
        return Err("界面更新包缺少入口或文件过多".into());
    }
    encoded
        .into_iter()
        .map(|(path, bytes)| {
            if !path.starts_with('/')
                || path.contains(['\\', '?', '#', '\0'])
                || path[1..]
                    .split('/')
                    .any(|s| s.is_empty() || s == "." || s == "..")
            {
                return Err("非法资源路径".into());
            }
            Ok((path, B64.decode(bytes).map_err(error)?))
        })
        .collect()
}
fn read_limited(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(error)?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(error)?;
    if bytes.len() > limit {
        return Err("缓存文件过大".into());
    }
    Ok(bytes)
}
fn rollback(disk: &mut DiskState) {
    if let Some(bad) = disk.current.take() {
        disk.rejected.push(bad);
    }
    disk.current = disk.previous.take();
    disk.pending = None;
}
fn pending_matches(disk: &DiskState, pending: &Option<String>) -> bool {
    pending.is_some() && disk.pending == *pending
}
fn atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let temp = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| {
        let mut f = File::create_new(&temp).map_err(error)?;
        f.write_all(bytes).map_err(error)?;
        f.sync_all().map_err(error)?;
        fs::rename(&temp, path).map_err(error)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
fn sources(url: &str) -> Result<Vec<String>> {
    let mut result = Vec::new();
    for prefix in MIRRORS.split(',').map(str::trim).filter(|v| !v.is_empty()) {
        let parsed = tauri::Url::parse(prefix).map_err(error)?;
        if parsed.scheme() != "https"
            || parsed.host_str().is_none()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
            || !parsed.username().is_empty()
        {
            return Err("镜像配置必须为 HTTPS 前缀".into());
        }
        result.push(format!("{}/{url}", prefix.trim_end_matches('/')));
    }
    result.push(url.into());
    Ok(result)
}
fn release_url(item: &Manifest, asset: &Asset) -> String {
    format!(
        "https://github.com/{REPOSITORY}/releases/download/v{}/{}",
        item.version, asset.name
    )
}
fn installer_kind() -> &'static str {
    use tauri::utils::config::BundleType;
    match tauri::utils::platform::bundle_type() {
        Some(BundleType::AppImage) => "appimage",
        Some(BundleType::Deb) => "deb",
        Some(BundleType::Msi) => "msi",
        Some(BundleType::Nsis) => "nsis",
        Some(BundleType::App) | Some(BundleType::Dmg) => "app",
        _ if cfg!(target_os = "macos") => "app",
        _ if cfg!(windows) => "nsis",
        _ => "deb",
    }
}
async fn download(
    url: &str,
    limit: usize,
    checking: bool,
    app: &tauri::AppHandle,
) -> Result<Vec<u8>> {
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(8))
        .read_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(if checking { 12 } else { 1800 }))
        .https_only(true)
        .user_agent("IntelligentMixVideo-Updater")
        .build()
        .map_err(error)?;
    let mut response = client
        .get(url)
        .header("Cache-Control", "no-cache")
        .send()
        .await
        .map_err(error)?
        .error_for_status()
        .map_err(error)?;
    if response
        .content_length()
        .is_some_and(|size| size > limit as u64)
    {
        return Err("下载内容超出声明大小".into());
    }
    let mut bytes = Vec::new();
    let mut reported = std::time::Instant::now();
    while let Some(chunk) = response.chunk().await.map_err(error)? {
        if bytes.len() + chunk.len() > limit {
            return Err("下载内容超出声明大小".into());
        }
        bytes.extend_from_slice(&chunk);
        if !checking && reported.elapsed() >= Duration::from_millis(200) {
            let _ = app.emit(
                "client-update-progress",
                json!({"received": bytes.len(), "total": limit}),
            );
            reported = std::time::Instant::now();
        }
    }
    Ok(bytes)
}
impl Updates {
    fn start<R: Runtime>(&self, app: &tauri::App<R>) -> Result<()> {
        if !enabled() {
            return Ok(());
        }
        let root = app.path().app_data_dir().map_err(error)?.join("updates");
        fs::create_dir_all(&root).map_err(error)?;
        let lock = File::options()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join("instances.lock"))
            .map_err(error)?;
        lock.try_lock_shared()
            .map_err(|_| "另一个客户端正在安装更新，请稍后启动")?;
        *self.instance.lock().unwrap() = Some(lock);
        self.root
            .set(root.clone())
            .map_err(|_| "更新存储重复初始化")?;
        let mut disk: DiskState = read_limited(&root.join("state.json"), 256 * 1024)
            .ok()
            .and_then(|v| serde_json::from_slice(&v).ok())
            .unwrap_or_default();
        if disk.pending.is_some() {
            rollback(&mut disk);
            *self.notice.lock().unwrap() = "上次界面更新未完成，已恢复可用版本".into();
        }
        *self.active.write().unwrap() = disk.current.as_ref().and_then(|v| self.load(v).ok());
        *self.disk.lock().unwrap() = disk;
        // 启动时不改共享指针；只有获得独占锁的应用/确认阶段写入。
        Ok(())
    }
    fn root(&self) -> Result<&Path> {
        self.root
            .get()
            .map(PathBuf::as_path)
            .ok_or_else(|| "更新存储不可用".into())
    }
    fn load(&self, name: &str) -> Result<Loaded> {
        version(name)?;
        let root = self.root()?;
        let item = manifest(
            &read_limited(&root.join(format!("{name}.manifest")), 256 * 1024)?,
            PUBLIC_KEY,
        )?;
        if item.version != name
            || !item.hot_validated
            || item.native_id != NATIVE_ID
            || version(name)? < version(env!("CARGO_PKG_VERSION"))?
        {
            return Err("缓存资源与当前原生程序不兼容".into());
        }
        let bytes = read_limited(&root.join(format!("{name}.resources")), MAX_HOT)?;
        Ok(Loaded {
            version: name.into(),
            files: hot_files(&item.hot, &bytes)?,
        })
    }
    fn save(&self, disk: &DiskState) -> Result<()> {
        atomic(
            &self.root()?.join("state.json"),
            &serde_json::to_vec(disk).map_err(error)?,
        )
    }
    fn current(&self) -> String {
        self.active
            .read()
            .unwrap()
            .as_ref()
            .map(|v| v.version.clone())
            .unwrap_or_else(|| env!("CARGO_PKG_VERSION").into())
    }
    fn mode(&self, item: &Manifest) -> String {
        if item.hot_validated
            && item.native_id == NATIVE_ID
            && !self.disk.lock().unwrap().rejected.contains(&item.version)
        {
            "hot"
        } else if installer_kind() == "deb" {
            "manual"
        } else {
            "native"
        }
        .into()
    }
    fn exclusive(&self) -> Result<()> {
        if self.exclusive_held.load(Ordering::SeqCst) {
            return Ok(());
        }
        // 先串行化锁升级，避免两个实例同时释放共享锁后误认为只有自己运行。
        let gate = File::options()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.root()?.join("apply.lock"))
            .map_err(error)?;
        gate.try_lock().map_err(|_| "另一个实例正在准备更新")?;
        let instance = self.instance.lock().unwrap();
        let lock = instance.as_ref().ok_or("更新存储不可用")?;
        lock.unlock().map_err(error)?;
        if lock.try_lock().is_err() {
            lock.lock_shared().map_err(error)?;
            return Err("请关闭其他客户端实例后重试应用更新".into());
        }
        self.exclusive_held.store(true, Ordering::SeqCst);
        Ok(())
    }
    fn shared(&self) {
        if !self.exclusive_held.swap(false, Ordering::SeqCst) {
            return;
        }
        if let Some(lock) = self.instance.lock().unwrap().as_ref() {
            let _ = lock.unlock();
            let _ = lock.lock_shared();
        }
    }
}

/// 所有命令共用一把进程内操作锁；前端不能指定下载 URL、公钥或安装文件。
#[tauri::command]
pub async fn client_update(
    app: tauri::AppHandle,
    operation: String,
    payload: Option<Value>,
) -> Result<Value> {
    let state = app.state::<Arc<Updates>>().inner().clone();
    let _operation = state
        .operation
        .try_lock()
        .map_err(|_| "另一个更新操作正在进行")?;
    let payload = payload.unwrap_or(Value::Null);
    if operation == "status" {
        return Ok(
            json!({"enabled": enabled() && state.root.get().is_some(), "nativeVersion": env!("CARGO_PKG_VERSION"), "version": state.current(), "notice": *state.notice.lock().unwrap(), "resume": state.disk.lock().unwrap().resume}),
        );
    }
    if !enabled() {
        return Err("此构建未启用正式客户端更新".into());
    }
    if operation == "ready" {
        if payload["version"].as_str() != Some(&state.current()) {
            return Err("页面版本与已应用资源不一致".into());
        }
        let mut disk = state.disk.lock().unwrap();
        if disk.pending.is_none()
            && disk.resume.is_none()
            && state.notice.lock().unwrap().is_empty()
        {
            return Ok(Value::Null);
        }
        state.exclusive()?;
        let mut next = disk.clone();
        next.pending = None;
        next.resume = None;
        // 写入失败仍保留待确认标记，让看门狗或下次启动回退。
        if let Err(e) = state.save(&next) {
            state.shared();
            return Err(e);
        }
        *disk = next;
        state.shared();
        return Ok(Value::Null);
    }
    if operation == "check" {
        let url = format!(
            "https://github.com/{REPOSITORY}/releases/latest/download/update-{TARGET}.json"
        );
        let mut best: Option<(Manifest, Vec<u8>)> = None;
        for url in sources(&url)? {
            if let Ok(bytes) = download(&url, 256 * 1024, true, &app).await {
                if let Ok(item) = manifest(&bytes, PUBLIC_KEY) {
                    if best.as_ref().is_none_or(|(old, _)| {
                        version(&item.version).unwrap() > version(&old.version).unwrap()
                    }) {
                        best = Some((item, bytes));
                    }
                }
            }
        }
        let (item, bytes) = best.ok_or("检查更新失败：所有来源均不可用或清单验证失败")?;
        if version(&item.version)? <= version(&state.current())? {
            *state.candidate.lock().unwrap() = None;
            return Ok(Value::Null);
        }
        let mode = state.mode(&item);
        if mode != "hot" && !item.installers.contains_key(installer_kind()) {
            return Err("新版缺少本机安装包".into());
        }
        let result = json!({"version": item.version, "mode": mode});
        *state.candidate.lock().unwrap() = Some((item, bytes));
        return Ok(result);
    }
    if operation == "download" {
        let (item, envelope) = state
            .candidate
            .lock()
            .unwrap()
            .clone()
            .ok_or("请先检查更新")?;
        let mode = state.mode(&item);
        if mode == "manual" {
            let asset = item.installers.get("deb").ok_or("缺少 deb 安装包")?;
            return Ok(
                json!({"links": sources(&release_url(&item, asset))?, "sha256": asset.sha256}),
            );
        }
        let asset = if mode == "hot" {
            &item.hot
        } else {
            item.installers.get(installer_kind()).ok_or("缺少安装包")?
        };
        if mode == "hot" && asset.size > MAX_HOT {
            return Err("界面更新包过大".into());
        }
        for url in sources(&release_url(&item, asset))? {
            if let Ok(bytes) = download(&url, asset.size, false, &app).await {
                if check_asset(asset, &bytes).is_err() {
                    continue;
                }
                if mode == "hot" {
                    if hot_files(asset, &bytes).is_err() {
                        continue;
                    }
                    atomic(
                        &state.root()?.join(format!("{}.manifest", item.version)),
                        &envelope,
                    )?;
                    atomic(
                        &state.root()?.join(format!("{}.resources", item.version)),
                        &bytes,
                    )?;
                } else {
                    if native_update(&app, &item, asset)?.verify(&bytes).is_err() {
                        continue;
                    }
                }
                *state.staged.lock().unwrap() = Some((item, bytes, mode));
                return Ok(Value::Null);
            }
        }
        return Err("下载失败：所有来源均不可用或文件校验失败，请重试".into());
    }
    if operation == "apply" {
        if serde_json::to_vec(&payload).map_err(error)?.len() > 16 * 1024 {
            return Err("恢复信息过大".into());
        }
        let pending =
            crate::remotion_sessions::remotion_sessions(app.clone(), "pending".into(), json!({}))
                .await?;
        if pending.as_i64() != Some(0) {
            return Err("仍有后台任务或状态不明的会话，请稍后应用".into());
        }
        state.exclusive()?;
        let staged = state.staged.lock().unwrap().take();
        let Some((item, bytes, mode)) = staged else {
            state.shared();
            return Err("请先下载更新".into());
        };
        let apply = (|| {
            let mut disk = state.disk.lock().unwrap();
            let mut next = disk.clone();
            next.resume = Some(payload.clone());
            if mode == "hot" {
                let loaded = state.load(&item.version)?;
                next.previous = disk.current.clone();
                next.current = Some(item.version.clone());
                next.pending = Some(uuid::Uuid::new_v4().to_string());
                state.save(&next)?;
                *state.active.write().unwrap() = Some(loaded);
            } else {
                state.save(&next)?;
            }
            *disk = next;
            Ok(())
        })();
        if let Err(e) = apply {
            *state.staged.lock().unwrap() = Some((item, bytes, mode));
            state.shared();
            return Err(e);
        }
        if mode == "hot" {
            let watchdog_app = app.clone();
            let state = state.clone();
            let pending = state.disk.lock().unwrap().pending.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(60)).await;
                let mut disk = state.disk.lock().unwrap();
                if !pending_matches(&disk, &pending) {
                    return;
                }
                if state.exclusive().is_err() {
                    return;
                }
                rollback(&mut disk);
                *state.active.write().unwrap() =
                    disk.current.as_ref().and_then(|v| state.load(v).ok());
                *state.notice.lock().unwrap() =
                    "新版页面未能启动，已恢复上一版本；可以尝试完整更新".into();
                let _ = state.save(&disk);
                state.shared();
                if let Some(window) = watchdog_app.get_webview_window("main") {
                    let _ = window.reload();
                }
            });
            app.get_webview_window("main")
                .ok_or("主窗口不可用")?
                .reload()
                .map_err(error)?;
        } else {
            let result = async {
                let update = native_update(
                    &app,
                    &item,
                    item.installers.get(installer_kind()).ok_or("缺少安装包")?,
                )?;
                tauri::async_runtime::spawn_blocking(move || update.install(bytes))
                    .await
                    .map_err(error)?
                    .map_err(error)
            }
            .await;
            if let Err(e) = result {
                state.shared();
                return Err(e);
            }
            app.restart();
        }
        return Ok(Value::Null);
    }
    Err("未知更新操作".into())
}
fn native_update<R: Runtime>(
    app: &tauri::AppHandle<R>,
    item: &Manifest,
    asset: &Asset,
) -> Result<tauri_plugin_updater::Update> {
    app.updater_builder().build().map_err(error)?
        .restore_update(json!({"version": item.version, "url": release_url(item, asset), "signature": asset.signature})).map_err(error)?.ok_or_else(|| "整包版本不高于当前原生版本".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixtures() -> Value {
        serde_json::from_str(include_str!("../tests/update-signatures.json")).unwrap()
    }
    fn signed() -> (Value, Manifest) {
        let fixture = fixtures();
        let item = manifest(
            &serde_json::to_vec(&fixture["manifests"][TARGET]).unwrap(),
            fixture["public_key"].as_str().unwrap(),
        )
        .unwrap();
        (fixture, item)
    }
    fn asset(bytes: &[u8]) -> Asset {
        Asset {
            name: "resources.json".into(),
            size: bytes.len(),
            sha256: format!("{:x}", Sha256::digest(bytes)),
            signature: String::new(),
        }
    }
    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("imv-updates-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn state(&self) -> Updates {
            let state = Updates::default();
            state.root.set(self.0.clone()).unwrap();
            let lock = File::options()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(self.0.join("instances.lock"))
                .unwrap();
            lock.try_lock_shared().unwrap();
            *state.instance.lock().unwrap() = Some(lock);
            state
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    /// 镜像篡改了版本、签名或资源字节时拒绝，合法签名包可读取。
    #[test]
    fn signed_manifest_and_resource_integrity() {
        let (fixture, item) = signed();
        let key = fixture["public_key"].as_str().unwrap();
        let bytes = B64.decode(fixture["resources"].as_str().unwrap()).unwrap();
        assert_eq!(
            hot_files(&item.hot, &bytes).unwrap()["/index.html"],
            b"<html>v2</html>"
        );
        let mut envelope: Envelope =
            serde_json::from_value(fixture["manifests"][TARGET].clone()).unwrap();
        let mut payload: Value =
            serde_json::from_slice(&B64.decode(&envelope.payload).unwrap()).unwrap();
        payload["version"] = json!("100.0.0");
        envelope.payload = B64.encode(serde_json::to_vec(&payload).unwrap());
        assert!(manifest(&serde_json::to_vec(&envelope).unwrap(), key).is_err());
        assert!(manifest(b"<html>mirror error</html>", key).is_err());
        let mut broken = bytes.clone();
        broken[0] ^= 1;
        assert!(hot_files(&item.hot, &broken).is_err());
        assert!(version("2.0.0-beta.1").is_err());
        assert!(version("2.0.0+test").is_err());
    }
    /// 即使摘要匹配，也不接受缺少入口、目录穿越或异常编码的资源。
    #[test]
    fn invalid_resource_paths_are_rejected() {
        for path in [
            "/../escape.js",
            "/a/./b",
            "/a//b",
            "/bad\\file",
            "/bad?x",
            "relative",
        ] {
            let mut files = json!({"/index.html":"eA==","/template-preview.html":"eA=="});
            files[path] = json!("eA==");
            let bytes = serde_json::to_vec(&files).unwrap();
            assert!(hot_files(&asset(&bytes), &bytes).is_err(), "{path}");
        }
        for files in [
            json!({"/index.html":"eA=="}),
            json!({"/index.html":"invalid!","/template-preview.html":"eA=="}),
        ] {
            let bytes = serde_json::to_vec(&files).unwrap();
            assert!(hot_files(&asset(&bytes), &bytes).is_err());
        }
    }
    /// 同一安装的另一实例持有共享锁时不能应用，失败后仍可重试且不留下独占锁。
    #[test]
    fn other_instances_block_apply_and_release_allows_retry() {
        let scratch = Scratch::new();
        let first = scratch.state();
        let second = scratch.state();
        assert!(first.exclusive().is_err());
        assert!(second.exclusive().is_err());
        drop(second);
        first.exclusive().unwrap();
        let third = File::options()
            .read(true)
            .write(true)
            .open(scratch.0.join("instances.lock"))
            .unwrap();
        assert!(third.try_lock_shared().is_err());
        first.shared();
        third.try_lock_shared().unwrap();
    }
    /// 崩溃待确认指针回退到上一版，保留恢复位置；前一次看门狗不能撤回后一次更新。
    #[test]
    fn interrupted_update_rolls_back_without_losing_resume() {
        let scratch = Scratch::new();
        let state = scratch.state();
        let mut disk = DiskState {
            current: Some("2.0.0".into()),
            previous: Some("1.0.0".into()),
            pending: Some("second".into()),
            resume: Some(json!({"workspace":"library"})),
            ..Default::default()
        };
        assert!(!pending_matches(&disk, &Some("first".into())));
        assert!(pending_matches(&disk, &Some("second".into())));
        rollback(&mut disk);
        state.save(&disk).unwrap();
        let saved: DiskState =
            serde_json::from_slice(&read_limited(&scratch.0.join("state.json"), 1024).unwrap())
                .unwrap();
        assert_eq!(saved.current.as_deref(), Some("1.0.0"));
        assert_eq!(saved.rejected, vec!["2.0.0"]);
        assert_eq!(saved.resume, disk.resume);
        assert!(!pending_matches(&saved, &None));
        assert!(read_limited(&scratch.0.join("state.json"), 2).is_err());
    }
    struct Bundled;
    impl<R: Runtime> Assets<R> for Bundled {
        fn get(&self, key: &AssetKey) -> Option<Cow<'_, [u8]>> {
            match key.as_ref() {
                "/index.html" => Some(Cow::Borrowed(b"<html>v1</html>")),
                "/old.js" => Some(Cow::Borrowed(b"old")),
                _ => None,
            }
        }
        fn iter(&self) -> Box<AssetsIter<'_>> {
            Box::new(std::iter::empty())
        }
        fn csp_hashes(&self, _: &AssetKey) -> Box<dyn Iterator<Item = CspHash<'_>> + '_> {
            Box::new(std::iter::empty())
        }
    }
    /// 实际 Tauri AssetResolver 读取更新资源、不混入旧资源；官方插件配置及签名版本绑定可用。
    #[test]
    fn asset_resolver_switches_and_official_updater_checks_signed_version() {
        let (fixture, mut item) = signed();
        let mut context = tauri::test::mock_context(Bundled);
        context.config_mut().app.windows.clear();
        let state = attach(&mut context);
        context.config_mut().plugins.0.get_mut("updater").unwrap()["pubkey"] =
            fixture["public_key"].clone();
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context)
            .unwrap();
        let resolver = app.asset_resolver();
        assert_eq!(
            resolver.get("index.html".into()).unwrap().bytes,
            b"<html>v1</html>"
        );
        *state.active.write().unwrap() = Some(Loaded {
            version: item.version.clone(),
            files: hot_files(
                &item.hot,
                &B64.decode(fixture["resources"].as_str().unwrap()).unwrap(),
            )
            .unwrap(),
        });
        assert_eq!(
            resolver.get("index.html".into()).unwrap().bytes,
            b"<html>v2</html>"
        );
        // AssetResolver 的 SPA 回退只会回到新 index，不得拿到旧 JS。
        assert_ne!(
            resolver.get("old.js".into()).map(|v| v.bytes),
            Some(b"old".to_vec())
        );
        *state.active.write().unwrap() = None;
        assert_eq!(
            resolver.get("index.html".into()).unwrap().bytes,
            b"<html>v1</html>"
        );
        let bytes = B64.decode(fixture["installer"].as_str().unwrap()).unwrap();
        let asset = item.installers.get(installer_kind()).unwrap().clone();
        native_update(app.handle(), &item, &asset)
            .unwrap()
            .verify(&bytes)
            .unwrap();
        item.version = "100.0.0".into();
        assert!(native_update(app.handle(), &item, &asset)
            .unwrap()
            .verify(&bytes)
            .is_err());
    }
}
