//! 发版工具与 build.rs 共用的原生摘要和资源打包逻辑；只访问给定目录，不读取业务配置。
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

/// 只遍历普通文件，不跟随符号链接；排序使资源包和指纹可重复生成。
pub fn files(dir: &Path) -> Result<Vec<PathBuf>> {
    let mut result = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_dir() {
            result.extend(files(&entry.path())?);
        } else if kind.is_file() {
            result.push(entry.path());
        } else {
            return Err(format!("不是普通文件：{}", entry.path().display()).into());
        }
    }
    result.sort();
    Ok(result)
}
/// 仅忽略项目自身版本字段，依赖版本、权限、桥接、工具链和内嵌目录均保留。
pub fn fingerprint(root: &Path, build: Value) -> Result<String> {
    let mut inputs = Vec::new();
    for dir in [
        "client/src-tauri/src",
        "client/src-tauri/capabilities",
        "client/src-tauri/permissions",
        "client/src-tauri/icons",
        "client/src-tauri/update-tools",
        ".github/scripts",
    ] {
        inputs.extend(files(&root.join(dir))?);
    }
    for file in [
        "client/src-tauri/build.rs",
        "client/src-tauri/Info.plist",
        "client/src-tauri/Cargo.toml",
        "client/src-tauri/Cargo.lock",
        "client/src-tauri/tauri.conf.json",
        "client/bun.lock",
        ".github/workflows/client-build.yml",
        "server/src/server/template/fonts.json",
        "server/src/server/template/sdk_catalog.json",
        "server/src/server/template/motions.json",
    ] {
        inputs.push(root.join(file));
    }
    inputs.sort();
    let mut hash = Sha256::new();
    for path in inputs {
        let name = path
            .strip_prefix(root)?
            .to_string_lossy()
            .replace('\\', "/");
        let mut bytes = fs::read(&path)?;
        if name == "client/src-tauri/tauri.conf.json" {
            let mut config: Value = serde_json::from_slice(&bytes)?;
            config
                .as_object_mut()
                .ok_or("Tauri 配置必须为对象")?
                .remove("version");
            bytes = serde_json::to_vec(&config)?;
        } else if name == "client/src-tauri/Cargo.toml" || name == "client/src-tauri/Cargo.lock" {
            let text = String::from_utf8(bytes)?;
            let mut own = false;
            let mut normalized = Vec::new();
            for line in text.lines() {
                if line.starts_with('[') {
                    own = line == "[package]";
                }
                if line == "name = \"client\"" {
                    own = true;
                }
                normalized.push(if own && line.starts_with("version = ") {
                    "version = \"0.0.0\""
                } else {
                    line
                });
            }
            bytes = normalized.join("\n").into_bytes();
        }
        hash.update(name);
        hash.update([0]);
        hash.update(bytes);
        hash.update([0]);
    }
    let package: Value = serde_json::from_slice(&fs::read(root.join("client/package.json"))?)?;
    hash.update(serde_json::to_vec(&json!({"packageManager":package["packageManager"],"tauriApi":package["dependencies"]["@tauri-apps/api"],"build":build}))?);
    Ok(format!("{:x}", hash.finalize()))
}
/// 一个有体积上限的 JSON 资源包，不解压文件，也就没有归档穿越或符号链接写入。
pub fn pack(dist: &Path) -> Result<Vec<u8>> {
    let mut encoded = BTreeMap::new();
    for path in files(dist)? {
        let key = format!(
            "/{}",
            path.strip_prefix(dist)?
                .to_string_lossy()
                .replace('\\', "/")
        );
        encoded.insert(key, B64.encode(fs::read(path)?));
    }
    if !encoded.contains_key("/index.html")
        || !encoded.contains_key("/template-preview.html")
        || encoded.len() > 4096
    {
        return Err("资源包缺少入口或文件过多".into());
    }
    let bytes = serde_json::to_vec(&encoded)?;
    if bytes.len() > 64 * 1024 * 1024 {
        return Err("资源包超过 64 MiB".into());
    }
    Ok(bytes)
}
/// 签名清单记录实际文件长度和内容摘要；文件名不含目录。
pub fn asset(path: &Path, signature: &str) -> Result<Value> {
    let bytes = fs::read(path)?;
    if bytes.is_empty() || bytes.len() > 1024 * 1024 * 1024 {
        return Err("更新资产为空或过大".into());
    }
    Ok(
        json!({"name":path.file_name().ok_or("文件名为空")?.to_str().ok_or("文件名不是 UTF-8")?,"size":bytes.len(),"sha256":format!("{:x}",Sha256::digest(&bytes)),"signature":signature}),
    )
}
/// 发布前用将内置到客户端的公钥复核签名，防止 CI 配错密钥后发布不可更新的产物。
pub fn verify(bytes: &[u8], signature: &str, public_key: &str) -> Result<()> {
    let key = String::from_utf8(B64.decode(public_key.trim())?)?;
    let sig = String::from_utf8(B64.decode(signature.trim())?)?;
    minisign_verify::PublicKey::decode(&key)?.verify(
        bytes,
        &minisign_verify::Signature::decode(&sig)?,
        true,
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn temp() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "imv-update-build-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
    fn put(root: &Path, path: &str, value: &str) {
        let path = root.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, value).unwrap();
    }
    /// 场景：资源内容原样打包、缺少入口即失败，不发布不完整前端。
    #[test]
    fn resource_package_requires_entries_and_preserves_bytes() {
        let root = temp();
        put(&root, "index.html", "new");
        assert!(pack(&root).is_err());
        put(&root, "template-preview.html", "preview");
        let value: Value = serde_json::from_slice(&pack(&root).unwrap()).unwrap();
        assert_eq!(
            B64.decode(value["/index.html"].as_str().unwrap()).unwrap(),
            b"new"
        );
        fs::remove_dir_all(root).unwrap();
    }
    /// 场景：发版版本号不影响兼容；原生命令或编译进壳的服务端目录变化会改变指纹。
    #[test]
    fn fingerprint_ignores_own_version_but_tracks_native_inputs() {
        let root = temp();
        for dir in [
            "client/src-tauri/src",
            "client/src-tauri/capabilities",
            "client/src-tauri/permissions",
            "client/src-tauri/icons",
            "client/src-tauri/update-tools",
            ".github/scripts",
        ] {
            fs::create_dir_all(root.join(dir)).unwrap();
        }
        for file in [
            "client/src-tauri/build.rs",
            "client/src-tauri/Info.plist",
            "client/bun.lock",
            ".github/workflows/client-build.yml",
            "server/src/server/template/fonts.json",
            "server/src/server/template/sdk_catalog.json",
            "server/src/server/template/motions.json",
        ] {
            put(&root, file, "");
        }
        put(
            &root,
            "client/package.json",
            r#"{"dependencies":{"@tauri-apps/api":"^2"}}"#,
        );
        for version in ["1.0.0", "2.0.0"] {
            put(
                &root,
                "client/src-tauri/Cargo.toml",
                &format!("[package]\nname = \"client\"\nversion = \"{version}\""),
            );
            put(&root,"client/src-tauri/Cargo.lock",&format!("[[package]]\nname = \"client\"\nversion = \"{version}\"\n[[package]]\nname = \"third-party\"\nversion = \"3.0.0\""));
            put(
                &root,
                "client/src-tauri/tauri.conf.json",
                &format!(r#"{{"version":"{version}"}}"#),
            );
            let id = fingerprint(&root, Value::Null).unwrap();
            if version == "1.0.0" {
                put(&root, "expected", &id);
            } else {
                assert_eq!(id, fs::read_to_string(root.join("expected")).unwrap());
            }
        }
        let old = fingerprint(&root, Value::Null).unwrap();
        put(&root, "server/src/server/template/fonts.json", "changed");
        assert_ne!(old, fingerprint(&root, Value::Null).unwrap());
        put(&root, "server/src/server/template/fonts.json", "");
        put(&root, "client/src-tauri/src/command.rs", "new command");
        assert_ne!(old, fingerprint(&root, Value::Null).unwrap());
        fs::remove_dir_all(root).unwrap();
    }
}
