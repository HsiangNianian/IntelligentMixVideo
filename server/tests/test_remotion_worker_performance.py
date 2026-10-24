"""执行真实叠加 worker，通过替身渲染器验证帧并发传递与可区分串行/流水线的性能回执。"""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("concurrency,parallel,shared", [(None, False, False), (4, False, False), (8, True, False), (16, True, True)])
@pytest.mark.parametrize("chunk_concurrency", [1, 2, 6])
def test_worker_reports_each_range_and_requested_page_budget(tmp_path: Path, concurrency, parallel, shared, chunk_concurrency):
    """页数原样传递、分块并发有界；保留进度 API 原始耗时，以墙钟比较串行与流水线。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("需要 Node 执行 worker")
    renderer = Path(__file__).parents[1] / "src/server/remotion"
    stub = tmp_path / "renderer-stub.mjs"
    stub.write_text('''/** 只记录宿主传入的调度参数，不执行生成源码。 */
import fs from 'node:fs/promises';
export const bundle = async () => 'local-bundle';
export const openBrowser = async () => ({close: async () => {}});
export const selectComposition = async () => ({id:'Overlay'});
let active = 0, peak = 0;
export const renderMedia = async (options) => {
  peak = Math.max(peak, ++active);
  const encoderArgs = options.ffmpegOverride({type: 'pre-stitcher', args: [
    '-threads:v', '1', '-f', process.env.TEST_SHARED === 'true' ? 'remotionshm' : process.env.TEST_PARALLEL === 'true' ? 'image2pipe' : 'image2',
    '-i', 'input', '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', 'out.webm',
  ]});
  const copyArgs = options.ffmpegOverride({type:'stitcher', args:['-i', 'input', '-c:v', 'copy', 'out.webm']});
  await fs.appendFile(process.env.IMV_WORK_ROOT + '/calls.jsonl', JSON.stringify({
    concurrency: options.concurrency, frameRange: options.frameRange, outputLocation: options.outputLocation,
    codec: options.codec, pixelFormat: options.pixelFormat,
    encoderArgs, copyArgs, disableSharedMemoryCapture:options.disableSharedMemoryCapture,
    disallowParallelEncoding:options.disallowParallelEncoding,
  }) + '\\n');
  await new Promise(resolve => setTimeout(resolve, options.frameRange[0] === 0 ? 40 : 5));
  options.onStart({parallelEncoding: process.env.TEST_PARALLEL === 'true', resolvedConcurrency: options.concurrency});
  options.onProgress({renderedFrames: 15, encodedFrames: 0, renderedDoneIn: 200, encodedDoneIn: null});
  options.onProgress({renderedFrames: 15, encodedFrames: 15, renderedDoneIn: 200, encodedDoneIn: 150});
  active--;
  await fs.writeFile(process.env.IMV_WORK_ROOT + '/peak.json', JSON.stringify(peak));
};
''', encoding="utf-8")
    source = (renderer / "overlay-worker.mjs").read_text(encoding="utf-8")
    source = source.replace('"@remotion/bundler"', json.dumps(stub.as_uri()))
    source = source.replace('"@remotion/renderer"', json.dumps(stub.as_uri()))
    worker = tmp_path / "worker.mjs"
    worker.write_text(source, encoding="utf-8")
    request = {"composition": {"frames": 180}, "instances": [], "ranges": [[first, first + 14] for first in range(0, 180, 15)],
               "chunk_concurrency": chunk_concurrency, "vp9": {"cpu_used": 4, "threads": 2, "row_mt": True},
               "disable_shared_memory_capture": not shared, "disallow_parallel_encoding": not parallel}
    if concurrency is not None:
        request["concurrency"] = concurrency
    (tmp_path / "request.json").write_text(json.dumps(request), encoding="utf-8")
    subprocess.run(
        [node, str(worker)], check=True, capture_output=True, text=True, timeout=10,
        env={**os.environ, "IMV_WORK_ROOT": str(tmp_path), "IMV_RENDERER_ROOT": str(renderer),
             "TEST_PARALLEL": str(parallel).lower(), "TEST_SHARED": str(shared).lower()},
    )
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sorted(call["frameRange"] for call in calls) == request["ranges"]
    assert all(call["concurrency"] == (concurrency or 1) for call in calls)
    assert all((call["codec"], call["pixelFormat"]) == ("vp9", "yuva420p") for call in calls)
    assert json.loads((tmp_path / "peak.json").read_text()) == chunk_concurrency
    for call in calls:
        assert call["copyArgs"] == ["-i", "input", "-c:v", "copy", "out.webm"]
        assert call["encoderArgs"][:2] == ["-threads:v", "1"]
        assert call["encoderArgs"][-7:] == ["-cpu-used", "4", "-threads", "2", "-row-mt", "1", "out.webm"]
        assert call["disableSharedMemoryCapture"] is (not shared)
        assert call["disallowParallelEncoding"] is (not parallel)
        assert Path(call["outputLocation"]).name == f"chunk-{call['frameRange'][0] // 15}.webm"
    report = json.loads((tmp_path / "renderer.json").read_text())
    assert report["frames"] == 180
    metrics = report["performance"]
    assert all(metrics[key] >= 0 for key in ("bundle_ms", "browser_ms", "select_ms", "render_ms", "total_ms"))
    assert [chunk["frame_range"] for chunk in metrics["chunks"]] == request["ranges"]
    assert metrics["chunk_concurrency"] == chunk_concurrency
    for chunk in metrics["chunks"]:
        assert chunk["resolved_concurrency"] == (concurrency or 1)
        assert chunk["parallel_encoding"] is parallel
        assert chunk["shared_memory_capture"] is shared
        assert chunk["rendered_ms"] == 200
        assert chunk["encoded_ms"] == 150
        assert chunk["rendered_frames"] == chunk["encoded_frames"] == 15
        assert chunk["elapsed_ms"] >= 0
