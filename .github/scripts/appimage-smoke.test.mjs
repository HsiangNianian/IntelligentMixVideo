/** AppImage 包内容检查回归测试；执行 bun test ./.github/scripts，夹具不启动 GUI 或访问真实服务。 */
import { afterEach, beforeEach, test } from "bun:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { verifyAppDir } from "./appimage-smoke.mjs";

let root;

/** 创建最小包内容夹具，仅用于验证文件清单检查；不冒充真实二进制或启动测试。 */
function file(path) {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), "fixture");
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "imv-appimage-test-"));
  file("usr/bin/client");
  file("usr/lib/libwebkit2gtk-4.1.so.0");
  file("usr/lib/gstreamer-1.0/libgstautodetect.so");
  file("usr/lib/gstreamer-1.0/libgstisomp4.so");
  file("usr/lib/gstreamer-1.0/libgstlibav.so");
  file("usr/lib/gstreamer-1.0/libgstopengl.so");
  file("usr/lib/gstreamer-1.0/libgstplayback.so");
  file("usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner");
});

afterEach(() => rmSync(root, { recursive: true, force: true }));

// 保留必需文件、其他运行库与名称相似的普通资源时允许通过。
test("accepts required files without bundled host libraries", () => {
  file("usr/lib/libgstvideo-1.0.so.0");
  file("usr/share/libwayland-client.txt");
  file("usr/lib/gstreamer-1.0/libgstpulseaudio.so");
  file("usr/share/libpulse.txt");
  assert.doesNotThrow(() => verifyAppDir(root));
});

// 回归图形崩溃与音频时钟停滞；库目录或版本后缀变化也不能绕过检查。
test("rejects bundled Wayland and PulseAudio libraries in each library layout", () => {
  for (const path of [
    "usr/lib/libwayland-client.so.0",
    "usr/lib/libwayland-cursor.so.0",
    "usr/lib/libwayland-egl.so.1",
    "usr/lib/libwayland-server.so.0",
    "usr/lib64/libwayland-client.so.0.26.0",
    "usr/lib/x86_64-linux-gnu/libwayland-client.so",
    "usr/lib/libpulse.so.0",
    "usr/lib/libpulsecommon-15.99.so",
    "usr/lib/libpulse-mainloop-glib.so.0",
    "usr/lib64/libpulse.so.0.24.3",
    "usr/lib/x86_64-linux-gnu/pulseaudio/libpulsecommon-17.0.so",
  ]) {
    file(path);
    assert.throws(() => verifyAppDir(root), /must use host Wayland and PulseAudio libraries/);
    rmSync(join(root, path));
  }
});

// 不能用指向其他文件的库别名绕过检查。
test("rejects a bundled Wayland library symlink", () => {
  symlinkSync("libwebkit2gtk-4.1.so.0", join(root, "usr/lib/libwayland-client.so.0"));
  assert.throws(() => verifyAppDir(root), /must use host Wayland and PulseAudio libraries/);
});

// 缺少程序、WebKit 或媒体插件的残缺包，即使没有冲突库也不能上传。
test("rejects packages missing the client, WebKit or media dependencies", () => {
  for (const path of [
    "usr/bin/client", "usr/lib/libwebkit2gtk-4.1.so.0",
    "usr/lib/gstreamer-1.0/libgstautodetect.so",
    "usr/lib/gstreamer-1.0/libgstisomp4.so",
    "usr/lib/gstreamer-1.0/libgstlibav.so",
    "usr/lib/gstreamer-1.0/libgstopengl.so",
    "usr/lib/gstreamer-1.0/libgstplayback.so",
    "usr/lib/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner",
  ]) {
    rmSync(join(root, path));
    assert.throws(() => verifyAppDir(root), /Missing required AppImage file/);
    file(path);
  }
});
