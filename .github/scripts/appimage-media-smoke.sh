#!/usr/bin/env bash
# 在 Linux 构建机解包最终 AppImage，隔离插件与缓存，验证包内 H.264/AAC 解码及字幕支持。
# 用法：bash .github/scripts/appimage-media-smoke.sh path/to/app.AppImage；需要同构建环境的 gst 工具。
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 path/to/app.AppImage" >&2
  exit 2
fi
appimage=$(realpath "$1")
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
cd "$scratch"
"$appimage" --appimage-extract > /dev/null
export APPDIR="$scratch/squashfs-root"

# 复用产物的启动 hook；不允许宿主插件或已有注册缓存掩盖缺包问题。
# shellcheck disable=SC1091
source "$APPDIR/apprun-hooks/linuxdeploy-plugin-gstreamer.sh"
export GST_PLUGIN_SYSTEM_PATH_1_0="$APPDIR/usr/lib/gstreamer-1.0"
export GST_PLUGIN_PATH_1_0="$GST_PLUGIN_SYSTEM_PATH_1_0"
export GST_REGISTRY_1_0="$scratch/registry.bin"
export LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/lib/x86_64-linux-gnu:$APPDIR/lib:$APPDIR/lib/x86_64-linux-gnu"

# 同时检查实际可加载性与来源，不能仅根据文件名判断传递依赖完整。
for element in avdec_h264 avdec_aac h264parse qtdemux webvttenc; do
  details=$(timeout 20 gst-inspect-1.0 "$element")
  printf '%s\n' "$details" | grep -F "$APPDIR/usr/lib/gstreamer-1.0/"
done

# 生成有限长度的测试媒体再解码，无网络、音频设备或显示服务器依赖。
timeout 30 gst-launch-1.0 -q videotestsrc num-buffers=3 \
  ! video/x-raw,format=I420,width=64,height=64,framerate=10/1 \
  ! openh264enc ! h264parse ! avdec_h264 ! fakesink
timeout 30 gst-launch-1.0 -q audiotestsrc num-buffers=4 \
  ! audioconvert ! avenc_aac ! avdec_aac ! fakesink
echo "AppImage bundled H.264/AAC decoding and WebVTT checks passed"
