//! 使用临时密钥运行真实 Rust 发布工具与官方签名器，不使用生产密钥、不安装测试产物。
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use imv_update_build::verify;
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn put(root: &Path, path: &str, bytes: impl AsRef<[u8]>) {
    let path = root.join(path);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
}
fn run(command: &mut Command) {
    let output = command.output().unwrap();
    // 签名器输出可能包含测试私钥，失败时也不打印 stdout/stderr。
    assert!(
        output.status.success(),
        "test command failed: {}",
        output.status
    );
}
#[test]
fn release_tool_emits_verified_assets_for_every_target() {
    let source = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap();
    let dir = Scratch(std::env::temp_dir().join(format!(
            "imv-signed-release-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        )));
    fs::create_dir_all(&dir.0).unwrap();
    let key = dir.0.join("key");
    run(Command::new("bun")
        .current_dir(&source)
        .args(["run", "tauri", "signer", "generate", "--ci", "-p", "", "-w"])
        .arg(&key));
    let private = fs::read_to_string(&key).unwrap();
    let public = fs::read_to_string(dir.0.join("key.pub")).unwrap();
    let fixture: Value = serde_json::from_slice(
        &fs::read(source.join("src-tauri/tests/update-signatures.json")).unwrap(),
    )
    .unwrap();
    let payload = B64
        .decode(
            fixture["manifests"]["x86_64-unknown-linux-gnu"]["payload"]
                .as_str()
                .unwrap(),
        )
        .unwrap();
    let manifest: Value = serde_json::from_slice(&payload).unwrap();
    put(
        &dir.0,
        "package.json",
        serde_json::to_vec(&json!({"version":"99.0.0","scripts":{"tauri":"tauri"}})).unwrap(),
    );
    put(&dir.0, "dist/index.html", b"new page");
    put(&dir.0, "dist/template-preview.html", b"preview");
    put(&dir.0, "native-id", "0".repeat(64));
    put(&dir.0, "hot-update-validation.json", b"{}");
    for (folder, name, kind) in [
        ("appimage", "test.AppImage", "appimage"),
        ("deb", "test.deb", "deb"),
        ("nsis", "test.exe", "nsis"),
        ("msi", "test.msi", "msi"),
        ("macos", "test.app.tar.gz", "app"),
    ] {
        let relative = format!("bundle/{folder}/{name}");
        put(
            &dir.0,
            &relative,
            B64.decode(fixture["installer"].as_str().unwrap()).unwrap(),
        );
        if kind != "deb" {
            put(
                &dir.0,
                &format!("{relative}.sig"),
                manifest["installers"][kind]["signature"].as_str().unwrap(),
            );
        }
    }
    // 工具不能递归检查 AppImage 内部的链接。
    fs::create_dir_all(dir.0.join("bundle/appimage/test.AppDir")).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink("missing", dir.0.join("bundle/appimage/test.AppDir/AppRun"))
        .unwrap();
    let path = std::env::join_paths(
        std::iter::once(source.join("node_modules/.bin"))
            .chain(std::env::split_paths(&std::env::var_os("PATH").unwrap())),
    )
    .unwrap();
    // 安装包来自固定测试签名，发布前用对应公钥检查；新清单改用另一把私钥必须失败。
    let target = "x86_64-unknown-linux-gnu";
    let result = Command::new(env!("CARGO_BIN_EXE_imv-update-build"))
        .arg(&dir.0)
        .arg(target)
        .arg(dir.0.join("bundle"))
        .arg(dir.0.join("native-id"))
        .env("PATH", &path)
        .env("TAURI_SIGNING_PRIVATE_KEY", private.trim())
        .env("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
        .env(
            "IMV_UPDATE_PUBLIC_KEY",
            fixture["public_key"].as_str().unwrap(),
        )
        .output()
        .unwrap();
    assert!(!result.status.success());
    // 用临时密钥重新签全部测试安装包，验证完整产物链。
    for relative in [
        "bundle/appimage/test.AppImage",
        "bundle/nsis/test.exe",
        "bundle/msi/test.msi",
        "bundle/macos/test.app.tar.gz",
    ] {
        run(Command::new("bun")
            .current_dir(&source)
            .args(["run", "tauri", "signer", "sign", "--app-version", "99.0.0"])
            .arg(dir.0.join(relative))
            .env("TAURI_SIGNING_PRIVATE_KEY", private.trim())
            .env("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", ""));
    }
    for target in [
        "x86_64-unknown-linux-gnu",
        "x86_64-pc-windows-msvc",
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
    ] {
        run(Command::new(env!("CARGO_BIN_EXE_imv-update-build"))
            .arg(&dir.0)
            .arg(target)
            .arg(dir.0.join("bundle"))
            .arg(dir.0.join("native-id"))
            .env("PATH", &path)
            .env("TAURI_SIGNING_PRIVATE_KEY", private.trim())
            .env("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
            .env("IMV_UPDATE_PUBLIC_KEY", public.trim()));
        let output = dir.0.join("src-tauri/target/updates").join(target);
        let envelope: Value = serde_json::from_slice(
            &fs::read(output.join(format!("update-{target}.json"))).unwrap(),
        )
        .unwrap();
        let payload = B64.decode(envelope["payload"].as_str().unwrap()).unwrap();
        verify(&payload, envelope["signature"].as_str().unwrap(), &public).unwrap();
        let item: Value = serde_json::from_slice(&payload).unwrap();
        assert_eq!(item["target"], target);
        assert_eq!(item["hot_validated"], false);
        assert_eq!(
            fs::metadata(output.join(item["hot"]["name"].as_str().unwrap()))
                .unwrap()
                .len(),
            item["hot"]["size"].as_u64().unwrap()
        );
        if target.contains("apple") {
            assert!(output
                .join(item["installers"]["app"]["name"].as_str().unwrap())
                .is_file());
        }
    }
}
