//! 生成 Tauri 资源；Windows 应用和测试程序均嵌入 Common Controls v6 清单。

/// 在 Cargo 构建阶段生成 Tauri 上下文依赖。
fn main() {
    for name in [
        "IMV_UPDATE_PUBLIC_KEY",
        "IMV_UPDATE_REPOSITORY",
        "IMV_UPDATE_MIRRORS",
        "IMV_DEBUG",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    for path in [
        "src",
        "permissions",
        "capabilities",
        "icons",
        "Cargo.toml",
        "Cargo.lock",
        "tauri.conf.json",
        "Info.plist",
        "../bun.lock",
        "update-tools",
        "../../.github",
        "../../server/src/server/template",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
    let target = std::env::var("TARGET").unwrap();
    let toolchain =
        std::process::Command::new(std::env::var("RUSTC").unwrap_or_else(|_| "rustc".into()))
            .arg("-vV")
            .output()
            .unwrap();
    let native_id = imv_update_build::fingerprint(
        std::path::Path::new("../.."),
        serde_json::json!({
            "toolchain": String::from_utf8(toolchain.stdout).unwrap(), "target":target,
            "flags":std::env::var("CARGO_ENCODED_RUSTFLAGS").unwrap_or_default(),
            "publicKey":std::env::var("IMV_UPDATE_PUBLIC_KEY").unwrap_or_default()
        }),
    )
    .expect("native fingerprint failed");
    println!("cargo:rustc-env=IMV_NATIVE_ID={}", native_id.trim());
    println!("cargo:rustc-env=IMV_UPDATE_TARGET={target}");
    // 供发布脚本读取实际编译时的指纹，而非再次猜测构建环境。
    println!("cargo:rerun-if-env-changed=IMV_UPDATE_METADATA_PATH");
    if let Ok(path) = std::env::var("IMV_UPDATE_METADATA_PATH") {
        println!("cargo:rerun-if-changed={path}");
        std::fs::write(path, native_id.trim()).unwrap();
    }
    let out = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
    std::fs::write(out.join("update-native-id"), native_id.trim()).unwrap();
    let mut attributes = tauri_build::Attributes::new();
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // tauri-build 默认只向主程序链接清单，mock 测试会在加载时缺少 v6 入口。
        // 由 MSVC 为所有链接目标嵌入同一依赖，避免重复的 manifest 资源。
        // https://github.com/tauri-apps/tauri/issues/13419
        attributes = attributes
            .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'");
    }
    tauri_build::try_build(attributes).expect("failed to build Tauri resources");
}
