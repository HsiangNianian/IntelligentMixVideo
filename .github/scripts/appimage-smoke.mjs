/** 验证 AppImage 媒体依赖与中文输入法模块并排除冲突的宿主库；执行：bun .github/scripts/appimage-smoke.mjs <AppImage>。 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/** AppImage 启动钩子把 GTK 固定到随包的输入法清单；这些模块须随包提供，宿主模块与随包 GTK/GLib 版本不符。 */
const IM_CACHE = "usr/lib/gtk-3.0/3.0.0/immodules.cache";

/** 检查解包产物的必需文件、已登记的输入法与冲突库，包含 usr 下不同架构的库目录及符号链接。 */
export function verifyAppDir(root) {
  for (const path of [
    "usr/bin/client", "usr/lib/libwebkit2gtk-4.1.so.0",
    "usr/lib/gstreamer-1.0/libgstautodetect.so",
    "usr/lib/gstreamer-1.0/libgstisomp4.so",
    "usr/lib/gstreamer-1.0/libgstlibav.so",
    "usr/lib/gstreamer-1.0/libgstopengl.so",
    "usr/lib/gstreamer-1.0/libgstplayback.so",
    "usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner",
    IM_CACHE,
    "usr/lib/gtk-3.0/3.0.0/immodules/im-fcitx5.so", "usr/lib/libFcitx5GClient.so.2",
    "usr/lib/gtk-3.0/3.0.0/immodules/im-ibus.so", "usr/lib/libibus-1.0.so.5",
  ]) {
    assert(existsSync(join(root, path)), `Missing required AppImage file: ${path}`);
  }
  const cache = readFileSync(join(root, IM_CACHE), "utf8");
  for (const id of ["fcitx", "fcitx5", "ibus"]) {
    assert.match(cache, new RegExp(`^"${id}" `, "m"), `AppImage must register the ${id} GTK input method`);
  }
  // 检查实际产物，而非复制排除规则；也能捕获库目录变化后规则失效。
  const bundled = readdirSync(join(root, "usr"), { recursive: true })
    .filter((path) => /(^|\/)lib(?:wayland-[^/]+|pulse[^/]*)\.so(?:\.|$)/.test(path));
  assert.deepEqual(bundled, [], "AppImage must use host Wayland and PulseAudio libraries");
}

if (import.meta.main) {
  assert.equal(process.argv.length, 3, "Expected exactly one AppImage path");
  const appimage = resolve(process.argv[2]);
  const dir = mkdtempSync(join(tmpdir(), "imv-appimage-smoke-"));
  try {
    // 只解包、不启动 GUI；无需 FUSE，解包失败或超时必须阻止上传。
    execFileSync(appimage, ["--appimage-extract"], {
      cwd: dir, stdio: ["ignore", "ignore", "pipe"], timeout: 60_000,
    });
    verifyAppDir(join(dir, "squashfs-root"));
    if (process.env.IMV_DEBUG === "true") {
      // 验证最终安装包内的归档，不借用 CI 中待打包的 runtime 目录。
      const root = join(dir, "squashfs-root");
      const archive = readdirSync(root, { recursive: true }).find((path) => path.endsWith("/backend.tar"));
      assert(archive, "IMV_DEBUG AppImage must contain backend.tar");
      const runtime = join(dir, "runtime");
      mkdirSync(runtime);
      execFileSync("tar", ["-xf", join(root, archive), "-C", runtime], { timeout: 120_000 });
      const repository = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
      execFileSync("uv", ["run", "--locked", "--project", "server", "pytest", "server/tests/test_desktop.py", "-k", "bundle", "-v"], {
        cwd: repository,
        env: { ...process.env, IMV_TEST_BUNDLE: runtime, IMV_TEST_APPDIR: root },
        stdio: "inherit", timeout: 720_000,
      });
    }
    console.log("AppImage keeps client/WebKit/media plugins and fcitx5/ibus input methods, and excludes bundled Wayland/PulseAudio libraries");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
