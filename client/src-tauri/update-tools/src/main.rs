//! 正式发版后生成签名更新资产；私钥只由官方 Rust Tauri CLI 从 CI 环境读取。
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use imv_update_build::{asset, pack, verify, Result};
use serde_json::{json, Map, Value};
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};
fn main() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() != 4 {
        return Err(
            "usage: imv-update-build <client> <target> <bundle-dir> <native-id-file>".into(),
        );
    }
    let client = Path::new(&args[0]).canonicalize()?;
    let target = &args[1];
    let bundle = Path::new(&args[2]);
    let native_id = fs::read_to_string(&args[3])?.trim().to_string();
    if native_id.len() != 64 || !native_id.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("缺少实际编译的原生指纹".into());
    }
    let public_key = env::var("IMV_UPDATE_PUBLIC_KEY")?;
    if env::var("TAURI_SIGNING_PRIVATE_KEY")?.is_empty() || public_key.is_empty() {
        return Err("正式更新必须配置签名密钥".into());
    }
    let package: Value = serde_json::from_slice(&fs::read(client.join("package.json"))?)?;
    let version = package["version"].as_str().ok_or("缺少版本")?;
    if version.split('.').count() != 3 || version.split('.').any(|s| s.parse::<u16>().is_err()) {
        return Err("仅发布正式版本".into());
    }
    let output = client.join("src-tauri/target/updates").join(target);
    fs::create_dir_all(&output)?;
    let resources = output.join(format!("resources-{target}.json"));
    fs::write(&resources, pack(&client.join("dist"))?)?;
    let kinds: &[(&str, &str, &str)] = if target.contains("windows") {
        &[("nsis", ".exe", "nsis"), ("msi", ".msi", "msi")]
    } else if target.contains("linux") {
        &[
            ("appimage", ".AppImage", "appimage"),
            ("deb", ".deb", "deb"),
        ]
    } else {
        &[("app", ".app.tar.gz", "macos")]
    };
    let mut installers = Map::new();
    for (kind, extension, folder) in kinds {
        // 仅读取最终安装文件；AppDir/.app 的内部符号链接属于打包中间产物。
        let mut matches: Vec<PathBuf> = Vec::new();
        for entry in fs::read_dir(bundle.join(folder))? {
            let entry = entry?;
            if entry.file_type()?.is_file() && entry.path().to_string_lossy().ends_with(extension) {
                matches.push(entry.path());
            }
        }
        if matches.len() != 1 {
            return Err(format!("必须有且只有一个 {kind} 更新文件").into());
        }
        let mut path = matches[0].clone();
        let signature = if *kind == "deb" {
            String::new()
        } else {
            fs::read_to_string(format!("{}.sig", path.display()))?
                .trim()
                .into()
        };
        if *kind != "deb" {
            verify(&fs::read(&path)?, &signature, &public_key)?;
        }
        if *kind == "app" {
            let unique = output.join(format!("IntelligentMixVideo_{version}_{target}.app.tar.gz"));
            fs::copy(&path, &unique)?;
            fs::write(format!("{}.sig", unique.display()), &signature)?;
            path = unique;
        }
        installers.insert((*kind).into(), asset(&path, &signature)?);
    }
    let validation: Value =
        serde_json::from_slice(&fs::read(client.join("hot-update-validation.json"))?)?;
    let hot_validated = validation[version][target]
        .as_array()
        .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(&native_id)));
    let payload = serde_json::to_vec(
        &json!({"format":1,"version":version,"target":target,"native_id":native_id,"hot_validated":hot_validated,"hot":asset(&resources,"")?,"installers":installers}),
    )?;
    let temporary = output.join("manifest.payload");
    fs::write(&temporary, &payload)?;
    let result = Command::new("bun")
        .current_dir(&client)
        .args(["run", "tauri", "signer", "sign", "--app-version", version])
        .arg(&temporary)
        .output()?;
    if !result.status.success() {
        return Err("Tauri 清单签名失败，请检查 CI 签名配置".into());
    }
    let signature = fs::read_to_string(format!("{}.sig", temporary.display()))?;
    verify(&payload, &signature, &public_key)?;
    fs::write(
        output.join(format!("update-{target}.json")),
        serde_json::to_vec(&json!({"payload":B64.encode(&payload),"signature":signature.trim()}))?,
    )?;
    fs::remove_file(&temporary)?;
    fs::remove_file(format!("{}.sig", temporary.display()))?;
    println!(
        "已生成 {target} v{version} 更新产物；原生摘要 {native_id}；热更新验证：{hot_validated}"
    );
    Ok(())
}
