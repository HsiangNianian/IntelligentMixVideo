"""用 Node 执行真实 worker，验证 PNG 有序背压、GPU 识别、编码参数及性能回执。运行 pytest 本文件。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, PngImagePlugin
import pytest


# 异步倒序交回同块 PNG，模拟较快页面先完成；FFmpeg 的实际输入顺序必须仍为逐帧递增。
RENDERER_STUB = """
import fs from 'node:fs/promises';
const root = process.env.IMV_WORK_ROOT;
const calls = (entry) => fs.appendFile(root+'/calls.jsonl',JSON.stringify(entry)+'\\n');
export const bundle = async () => 'sealed-bundle';
export const recordFontLoad = () => {};
export const RenderInternals = {getExecutablePath: () => process.env.BUNDLED_FFMPEG};
export const verifyManagedFonts = async () => {
  if (process.env.FAIL === 'font') throw new Error('font unavailable');
  return {runtime:{test:true}};
};
export const openBrowser = async (_,opts) => {
  await calls({gl:opts.chromiumOptions.gl});
  let probes = 0;
  return {close:async()=>{}, connection:{send:async(method)=>{
    await calls({method});
    const later = ++probes > 1;
    if (later && process.env.CDP_CLOSED) throw new Error('CDP connection closed');
    if (later && process.env.CDP_HANG) return new Promise(() => {});
    const gl = (later ? process.env.GL_AFTER : null) ?? process.env.GL ?? 'ANGLE (NVIDIA, RTX 5060)';
    return {value:{gpu:{devices:[{vendorId:4318}],auxAttributes:{glRenderer:gl,processCrashCount:later ? Number(process.env.CRASH_AFTER ?? 0) : 0},
      featureStatus:{gpu_compositing:'enabled'}}}};
  }}};
};
export const selectComposition = async () => ({id:'Overlay'});
let active = 0, peak = 0;
export const renderFrames = async (options) => {
  peak=Math.max(peak,++active);
  await calls({range:options.frameRange, pages:options.concurrency,peak, imageFormat:options.imageFormat, outputDir:options.outputDir});
  if (process.env.FAIL === 'source') throw new Error("Sprite failed: GPU process isn't usable, NV_ENC_ERR");
  options.onStart({resolvedConcurrency:options.concurrency});
  const [first,last]=options.frameRange;
  const inputs=await Promise.all(Array.from({length:last-first+1},async (_,i)=> {
    const frame=last-i;
    return [await fs.readFile(root+'/frame-'+frame+'.png'),frame];
  }));
  await Promise.all(inputs.map(([buffer,frame]) => options.onFrameBuffer(buffer,frame)));
  active--;
};
"""

# 先暂停读管道，随后逐个解析 PNG；真实顺序、参数和目标文件都会留给断言核对。
FAKE_FFMPEG = '''
import io, json, os, pathlib, sys, time
from PIL import Image
if 'FFMPEG_ERROR' in os.environ:
    print(os.environ['FFMPEG_ERROR'], file=sys.stderr)
    sys.exit(1)
time.sleep(.04)
parts=sys.stdin.buffer.read().split(b'\\x89PNG\\r\\n\\x1a\\n')[1:]
indices=[Image.open(io.BytesIO(b'\\x89PNG\\r\\n\\x1a\\n'+part)).getpixel((0,0))[0] for part in parts]
if os.environ.get('FAIL') == 'disk':
    sys.stderr.write('Error writing trailer: No space left on device\\n')
    sys.exit(1)
args=sys.argv[1:]
outputs=[pathlib.Path(p) for p in args if p.endswith(('.mp4','.webm'))]
for p in outputs:
    p.write_text(json.dumps({'input':indices,'args':args,'binary':sys.argv[0]}))
print('frame='+str(len(parts)),flush=True)
'''


@pytest.fixture
def worker(tmp_path):
    """在临时目录接入浏览器与编码替身，worker 与管道代码均直接取自项目。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node 执行 worker")
    renderer = Path(__file__).parents[1] / "src/server/remotion"
    stub = tmp_path / "renderer-stub.mjs"
    stub.write_text(RENDERER_STUB)
    script = (renderer / "overlay-worker.mjs").read_text()
    for dependency in ("@remotion/bundler", "@remotion/renderer", "./managed-fonts.mjs"):
        script = script.replace(json.dumps(dependency), json.dumps(stub.as_uri()))
    script = script.replace('"./overlay-encoder.mjs"', json.dumps((renderer / "overlay-encoder.mjs").as_uri()))
    worker_file = tmp_path / "worker.mjs"
    worker_file.write_text(script)
    fake = tmp_path / "ffmpeg"
    fake.write_text(f"#!{sys.executable}\n" + FAKE_FFMPEG)
    fake.chmod(0o755)
    bundled = tmp_path / "bundled-ffmpeg"
    bundled.symlink_to(fake)

    def run(*, route=None, ranges=None, count=9, options=None, env=None, real=False, size=(24, 16)):
        """产生含帧号的 RGBA PNG，可选真实 FFmpeg 以解码检验透明度。"""
        for frame in range(count):
            meta = PngImagePlugin.PngInfo()
            if not real:
                meta.add_text("padding", "x" * 200000)  # 超过 Node pipe 的 highWaterMark。
            Image.new("RGBA", size, (frame, 80, 160, frame * 20)).save(
                tmp_path / f"frame-{frame}.png", pnginfo=meta)
        request = {"composition": {"width": size[0], "height": size[1], "frames": count, "duration_in_frames": count, "fps": 30},
                   "instances": [], "concurrency": 4, "chunk_concurrency": 4, **(options or {})}
        if route is not None:
            request["route"] = route
        if ranges is not None:
            request["ranges"] = ranges
        (tmp_path / "request.json").write_text(json.dumps(request))
        return subprocess.run([node, str(worker_file)], capture_output=True, text=True, timeout=20,
                              env={**os.environ, "IMV_WORK_ROOT": str(tmp_path), "IMV_RENDERER_ROOT": str(renderer),
                                   "BUNDLED_FFMPEG": str(bundled), "PATH": os.environ["PATH"] if real else
                                   str(tmp_path) + os.pathsep + os.environ["PATH"], **(env or {})})
    return run


@pytest.mark.parametrize("route,chunked,explicit", [
    (None, True, False), ("cpu-webm", False, True), ("gpu-webm", True, True),
    ("h264-pair", True, False), ("h264-pair", False, False),
])
def test_routes_write_ordered_frames_and_report_real_concurrency(tmp_path, worker, route, chunked, explicit):
    """三条路线都按绝对时钟写入每帧；GPU最多两块，共用浏览器，保持逐块进度可观测。"""
    ranges = [[0, 2], [3, 5], [6, 8]] if chunked else [[0, 8]]
    options = {"vp9": {"cpu_used": 4, "threads": 2, "row_mt": False}} if explicit else {}
    result = worker(route=route, ranges=ranges if chunked else None, options=options)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(row) for row in (tmp_path / "calls.jsonl").read_text().splitlines()]
    gpu = route in ("gpu-webm", "h264-pair")
    assert [c["gl"] for c in calls if "gl" in c] == (["angle-egl"] if gpu else ["swangle"])
    assert [c["method"] for c in calls if "method" in c] == (["SystemInfo.getInfo"] * (1 + len(ranges)) if gpu else [])
    grabs = [c for c in calls if "range" in c]
    expected_concurrency = min(len(ranges), 2 if gpu else 4)
    assert max(c["peak"] for c in grabs) == expected_concurrency
    assert sorted(c["range"] for c in grabs) == ranges
    assert all(c["pages"] == 4 and c["imageFormat"] == "png" and c["outputDir"] is None for c in grabs)
    for i, (first, last) in enumerate(ranges):
        if route == "h264-pair":
            prefix = f"chunk-{i}-" if chunked else ""
            outputs = [tmp_path / f"{prefix}{name}.mp4" for name in ("color", "mask")]
        else:
            outputs = [tmp_path / (f"chunk-{i}.webm" if chunked else "overlay.webm")]
        for output in outputs:
            encoded = json.loads(output.read_text())
            assert encoded["input"] == list(range(first, last + 1))
            assert Path(encoded["binary"]).name == ("ffmpeg" if route == "h264-pair" else "bundled-ffmpeg")
            args = encoded["args"]
            if route == "h264-pair":
                assert [args[n+1] for n, flag in enumerate(args) if flag == "-qp"] == ["24", "15"]
                assert [args[n+1] for n, flag in enumerate(args) if flag == "-color_range"] == ["tv", "pc"]
                assert args.count("h264_nvenc") == 2 and "alphaextract" in args[args.index("-filter_complex") + 1]
            else:
                assert "libvpx-vp9" in args and "yuva420p" in args
                for flag, value in (("-deadline", "realtime"), ("-cpu-used", "4" if explicit else "7"),
                                    ("-row-mt", "0" if explicit else "1"), ("-crf", "34"), ("-lag-in-frames", "0")):
                    assert args[args.index(flag)+1] == value
                assert [args[n+1] for n, flag in enumerate(args) if flag == "-threads"] == ["3", "2" if explicit else "3"]
    report = json.loads((tmp_path / "renderer.json").read_text())
    metrics = report["performance"]
    assert report["frames"] == 9 and report["runtime"]["test"]
    assert all(metrics[key] >= 0 for key in ("bundle_ms", "browser_ms", "select_ms", "render_ms", "total_ms"))
    assert metrics["chunk_concurrency"] == expected_concurrency
    for chunk, limits in zip(metrics["chunks"], ranges, strict=True):
        assert chunk["frame_range"] == limits
        assert chunk["resolved_concurrency"] == 4 and chunk["parallel_encoding"] and not chunk["shared_memory_capture"]
        assert chunk["rendered_frames"] == chunk["encoded_frames"] == limits[1] - limits[0] + 1
        assert all(chunk[key] >= 0 for key in ("elapsed_ms", "rendered_ms", "encoded_ms"))
    assert "[overlay-performance]" in result.stdout


@pytest.mark.parametrize("env,marker", [
    ({"GL": "ANGLE (Google, SwiftShader)"}, "graphics"), ({"GL": "ANGLE (Mesa, llvmpipe)"}, "graphics"),
    ({"FFMPEG_ERROR": "[h264_nvenc] OpenEncodeSessionEx failed: no capable devices"}, "encoder"),
    ({"FAIL": "source"}, None), ({"FAIL": "font"}, None), ({"FAIL": "disk"}, None),
    ({"GL_AFTER": "ANGLE (Google, SwiftShader)"}, "graphics"), ({"CRASH_AFTER": "1"}, "graphics"),
    ({"FAIL": "source", "GL_AFTER": "ANGLE (Mesa, llvmpipe)"}, "graphics"),
    ({"FAIL": "source", "CDP_CLOSED": "1"}, None), ({"FAIL": "source", "CDP_HANG": "1"}, None),
])
def test_only_identified_hardware_failures_allow_gpu_retry(tmp_path, worker, env, marker):
    """即使生成源码的错误含英伟达诊断字样，仍然保留原业务故障。"""
    result = worker(route="h264-pair", count=3, env=env)
    assert result.returncode != 0
    path = tmp_path / "gpu-failure.json"
    assert (json.loads(path.read_text()) if path.exists() else None) == ({"kind": marker} if marker else None)
    if env.get("FAIL") == "source" and not marker:
        assert "Sprite failed:" in result.stderr
    assert not (tmp_path / "renderer.json").exists()


@pytest.mark.parametrize("diagnostic,size_limit", [
    ("[h264_nvenc @ 0x1] InitializeEncoder failed: invalid param (8): Frame Dimension less than the minimum supported value.", True),
    ("[h264_nvenc @ 0x1] Width 8192 exceeds 4096\n[h264_nvenc @ 0x1] No capable devices found", True),
    ("[h264_nvenc @ 0x1] Height 8192 exceeds 4096", True),
    ("[h264_nvenc @ 0x1] InitializeEncoder failed: invalid param (8)", False),
    ("[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: invalid param (8)", False),
    ("[png @ 0x1] Width 8192 exceeds 4096", False),
])
def test_only_nvenc_dimension_limits_get_a_per_render_reason(tmp_path, worker, diagnostic, size_limit):
    """编码器报画布受限时可换 WebM，普通参数错误与 PNG 错误保留原诊断。"""
    result = worker(route="h264-pair", count=1, size=(32, 32), env={"FFMPEG_ERROR": diagnostic})
    assert result.returncode != 0 and diagnostic in result.stderr
    marker = tmp_path / "gpu-failure.json"
    if size_limit:
        assert json.loads(marker.read_text()) == {"kind": "encoder", "reason": "nvenc_dimensions_unsupported"}
    else:
        assert not marker.exists()


@pytest.mark.parametrize("route", ["cpu-webm", "gpu-webm"])
def test_fast_webm_keeps_alpha_when_path_ffmpeg_lacks_vp9(tmp_path, worker, route):
    """PATH 中的 FFmpeg 不支持 VP9 时，两条 WebM 路线仍能用内置编码器保留每帧透明度。"""
    renderer = Path(__file__).parents[1] / "src/server/remotion"
    if not (renderer / "node_modules/@remotion/renderer").exists():
        pytest.skip("需要锁定的 Remotion 编码二进制")
    ffmpeg = Path(subprocess.check_output(
        [shutil.which("node"), "--input-type=module", "-e",
         'import {RenderInternals} from "@remotion/renderer"; console.log(RenderInternals.getExecutablePath('
         '{type:"ffmpeg",indent:false,logLevel:"error",binariesDirectory:null}));'],
        cwd=renderer, text=True, timeout=10).strip())
    env = {"BUNDLED_FFMPEG": str(ffmpeg), "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "FFMPEG_ERROR": "Unknown encoder 'libvpx-vp9'"}
    width, height = 25, 17
    result = worker(route=route, count=5, real=True, env=env, size=(width, height))
    assert result.returncode == 0, result.stderr
    subprocess.run([ffmpeg, "-v", "error", "-c:v", "libvpx-vp9", "-i", tmp_path / "overlay.webm",
                    "-pix_fmt", "rgba", tmp_path / "decoded-%d.png"], check=True, timeout=10, cwd=ffmpeg.parent,
                   env={**os.environ, "DYLD_LIBRARY_PATH": str(ffmpeg.parent)} if sys.platform == "darwin" else None)
    decoded = sorted(tmp_path.glob("decoded-*.png"))
    assert len(decoded) == 5
    for i, frame in enumerate(decoded):
        with Image.open(frame) as image:
            assert image.size == (width, height)
            assert abs(image.getpixel((width // 2, height // 2))[3] - i * 20) <= 4


def test_bundled_ffmpeg_receives_its_darwin_library_directory(tmp_path, worker):
    """即使从其他目录启动 worker，macOS 编码子进程也能在自己的目录找到动态库。"""
    preload = tmp_path / "darwin.cjs"
    preload.write_text("""Object.defineProperty(process, 'platform', {value: 'darwin'});
const cp = require('node:child_process');
const original = cp.spawn;
cp.spawn = (file, args, options) => {
  const dir = require('node:path').dirname(process.env.BUNDLED_FFMPEG);
  if (options.cwd !== dir || options.env?.DYLD_LIBRARY_PATH !== dir) throw Error('missing FFmpeg library directory');
  return original(file, args, options);
};
require('node:module').syncBuiltinESMExports();
""")
    result = worker(count=1, env={"NODE_OPTIONS": f'--require="{preload}"'})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "overlay.webm").is_file()
