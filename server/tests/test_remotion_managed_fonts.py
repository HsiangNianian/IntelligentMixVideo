"""SC 字体回归：TTC 地区选择、字重与随包回退，以及 Chrome 实际字形校验后的自动恢复。

运行 `uv run --locked pytest tests/test_remotion_managed_fonts.py -v`；真实浏览器用例另需
IMV_TEST_RENDERER=1、IMV_BROWSER_EXECUTABLE 和 IMV_FONT_REGULAR（多地区 TTC），不访问模型或生产服务。
"""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fontTools.ttLib import TTCollection, TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image, ImageChops, ImageStat

from server.remotion_templates.managed_fonts import FAMILY, bundled_font, managed_font, prepare_fonts
from server.remotion_templates.overlay_render import Overlay, render_overlay, run_overlay_worker
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings

def sc_test_font(family: str, weight: int = 400, advance: int = 600) -> bytes:
    """Build valid SFNT tables with visible Chinese probe glyphs and distinctive metrics, without installed fonts."""
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "han"])
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((advance - 50, 0))
    pen.lineTo((advance - 50, 700))
    pen.lineTo((50, 700))
    pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "han": pen.glyph()})
    builder.setupCharacterMap({ord(character): "han" for character in "关复"})
    builder.setupHorizontalMetrics({name: (advance, 0) for name in (".notdef", "han")})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    style = "Bold" if weight == 700 else "Regular"
    builder.setupNameTable({"familyName": family, "styleName": style, "psName": family.replace(" ", "") + "-" + style,
                            "fullName": family + " " + style})
    builder.setupOS2(usWeightClass=weight, sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def collection_bytes(families: list[str], weight: int = 400) -> bytes:
    """Build a TTC with distinguishable metrics, ensuring tests verify the selected tables as well as the name."""
    collection = TTCollection()
    collection.fonts = [TTFont(BytesIO(sc_test_font(family, weight=weight, advance=500 + 100 * index)))
                        for index, family in enumerate(families)]
    output = BytesIO()
    try:
        collection.save(output)
        return output.getvalue()
    finally:
        collection.close()


@pytest.mark.parametrize("weight", [400, 700])
@pytest.mark.parametrize("families", [["Noto Sans CJK JP", "Noto Sans CJK KR", FAMILY], [FAMILY, "Noto Sans CJK JP"]])
def test_collection_selects_sc_by_name_not_index(tmp_path, weight, families):
    """JP 排在首位或 SC 顺序改变时，输出仍为独立 SC 字体，并保留该子字体的实际字宽与原始指纹。"""
    source = tmp_path / "font.ttc"
    source.write_bytes(collection_bytes(families, weight))
    selected = managed_font(source, weight)
    assert selected.data[:4] != b"ttcf"
    with TTFont(BytesIO(selected.data)) as font:
        assert font["name"].getBestFamilyName() == FAMILY
        assert font["OS/2"].usWeightClass == weight
        assert font["hmtx"].metrics[font.getBestCmap()[ord("关")]][0] == 500 + 100 * families.index(FAMILY)
    assert selected.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["jp", "wrong_weight", "corrupt", "truncated", "missing"])
def test_unusable_configuration_falls_back_without_failing(tmp_path, kind):
    """缺少 SC、字重不符、文件损坏或不存在时直接使用随包中文字体，不让字体选择错误阻断合成。"""
    source = tmp_path / "font.ttc"
    if kind != "missing":
        source.write_bytes({
            "jp": collection_bytes(["Noto Sans CJK JP"]),
            "wrong_weight": sc_test_font(FAMILY, weight=700),
            "corrupt": b"broken font",
            "truncated": sc_test_font(FAMILY)[:64],
        }[kind])
    font = managed_font(source, 400)
    assert font.data == bundled_font(400).data
    with TTFont(BytesIO(font.data)) as parsed:
        assert parsed["name"].getBestFamilyName() == "Noto Sans SC"
        assert all(ord(character) in parsed.getBestCmap() for character in "关复")


def test_replacing_a_font_invalidates_selection(tmp_path):
    """同一路径先为 JP 后换成 SC 时，内容缓存不沿用先前的回退结果。"""
    source = tmp_path / "font.ttf"
    source.write_bytes(sc_test_font("Noto Sans CJK JP"))
    assert managed_font(source, 400).data == bundled_font(400).data
    source.write_bytes(sc_test_font(FAMILY))
    assert managed_font(source, 400).data == source.read_bytes()


def test_overlay_preparation_recovers_from_japanese_only_font(tmp_path):
    """完整叠加入口用仅含 JP 的配置也会继续调用 worker，给编码器提供独立中文字体。"""
    source = tmp_path / "jp.ttc"
    source.write_bytes(collection_bytes(["Noto Sans CJK JP"]))
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=source, font_bold=source)

    class CaptureRenderer(Renderer):
        """仅替换进程执行，检查合成入口真实写入的字体后产出媒体替身。"""

        async def run_worker(self, directory, **kwargs):
            """两种字重均采用随包 SC 字体，原始错误配置不传给 worker。"""
            for weight in (400, 700):
                assert (directory / "public" / f"font-{weight}.otf").read_bytes() == bundled_font(weight).data
            (directory / "overlay.webm").write_bytes(b"rendered")
            return {}

    overlay = Overlay(width=320, height=180, fps=30, frames=1, instances=[])
    asyncio.run(run_overlay_worker(CaptureRenderer(settings), overlay, tmp_path / "render"))
    assert (tmp_path / "render" / "overlay.webm").read_bytes() == b"rendered"


BROWSER = os.environ.get("IMV_BROWSER_EXECUTABLE")
SYSTEM_FONT = os.environ.get("IMV_FONT_REGULAR")


needs_browser = pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1" or not BROWSER or not SYSTEM_FONT or not shutil.which("node"),
                                   reason="requires IMV_TEST_RENDERER=1, a browser and a multi-region TTC")


@needs_browser
def test_browser_detects_jp_glyphs_and_recovers_once(tmp_path):
    """真实 Chrome 对正确 SC 不回退；故意重现 TTC 首个 JP 字形后只切换一次并继续，随后校验稳定通过。"""
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=Path(SYSTEM_FONT), font_bold=Path(SYSTEM_FONT))
    prepare_fonts(settings, tmp_path)
    renderer = settings.renderer_dir
    script = tmp_path / "font-check.mjs"
    script.write_text(f'''/** Exercise production glyph checking with real Chrome, including the original TTC regression. */
import fs from "node:fs/promises";
import {{openBrowser}} from {json.dumps((renderer / "node_modules/@remotion/renderer/dist/esm/index.mjs").as_uri())};
import {{checkManagedFonts, verifyManagedFonts}} from {json.dumps((renderer / "managed-fonts.mjs").as_uri())};
const root = {json.dumps(str(tmp_path))};
console.info("opening browser");
const browser = await openBrowser("chrome", {{browserExecutable: {json.dumps(BROWSER)}, logLevel: "error", chromiumOptions: {{gl: "swangle", enableMultiProcessOnLinux: true}}}});
try {{
  console.info("checking SC faces");
  const initial = await verifyManagedFonts(browser, root);
  console.info("reproducing TTC first face");
  await fs.copyFile({json.dumps(SYSTEM_FONT)}, `${{root}}/public/font-700.otf`);
  const recovered = await verifyManagedFonts(browser, root);
  const stable = await verifyManagedFonts(browser, root);
  const page = await browser.newPage({{context:()=>null, logLevel:"error", indent:false, pageIndex:0, onBrowserLog:null, onLog:()=>undefined}});
  const client = page._client();
  const send = client.send.bind(client);
  // Simulate a browser without the diagnostic endpoint; fallback must finish even when its recheck also fails.
  client.send = (method, ...args) => {{ if (method === "CSS.getPlatformFontsForNode") throw new Error("Font diagnostics unavailable"); return send(method, ...args); }};
  const advisory = await checkManagedFonts(page, root);
  await page.close();
  await fs.writeFile(`${{root}}/result.json`, JSON.stringify({{initial, recovered, stable, advisory}}));
}} finally {{ await browser.close({{silent:true}}); }}
''', encoding="utf-8")
    log = tmp_path / "browser.log"
    with log.open("wb") as output:
        process = subprocess.Popen(["node", str(script)], stdout=output, stderr=output, start_new_session=True)
        try:
            assert process.wait(timeout=120) == 0, log.read_text()
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    results = json.loads((tmp_path / "result.json").read_text())
    assert {name: result["fallbackWeights"] for name, result in results.items()} == {
        "initial": [], "recovered": [700], "stable": [], "advisory": [400, 700],
    }
    recovered = results["recovered"]["runtime"]
    assert recovered["font_fallback_weights"] == "[700]"
    assert recovered["font_400_selection"] == "configured" and recovered["font_700_selection"] == "bundled"
    assert recovered["font_700_sha256"] == hashlib.sha256(bundled_font(700).data).hexdigest()
    assert (tmp_path / "public/font-700.otf").read_bytes() == bundled_font(700).data


@pytest.fixture(scope="module")
def glyph_references(tmp_path_factory):
    """Draw independent SC, JP and square-glyph controls in Chrome; none uses production font selection/loaders."""
    root = tmp_path_factory.mktemp("glyph-references")
    (root / "sc.otf").write_bytes(bundled_font(700).data)
    with TTCollection(SYSTEM_FONT) as collection:
        next(face for face in collection.fonts if face["name"].getBestFamilyName() == "Noto Sans CJK JP").save(root / "jp.otf")
    (root / "tofu.otf").write_bytes(sc_test_font("Reference Squares", weight=700))
    renderer = Settings(_env_file=None).renderer_dir
    script = root / "references.mjs"
    script.write_text(fr'''/** Independent browser glyph references for decoded-video comparison. */
import fs from 'node:fs/promises';
import http from 'node:http';
import {{openBrowser}} from {json.dumps((renderer / "node_modules/@remotion/renderer/dist/esm/index.mjs").as_uri())};
const root = {json.dumps(str(root))};
const server=http.createServer(async (req,res)=>{{
  if (/^\/(sc|jp|tofu)\.otf$/.test(req.url)) {{res.end(await fs.readFile(root+req.url));}}
  else res.end('<!doctype html><html><body style="margin:0;background:black"></body></html>');
}});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+server.address().port;
let browser;
try {{
  browser=await openBrowser('chrome',{{browserExecutable:{json.dumps(BROWSER)},logLevel:'error',chromiumOptions:{{gl:'swangle',enableMultiProcessOnLinux:true}}}});
  const page=await browser.newPage({{context:()=>null,logLevel:'error',indent:false,pageIndex:0,onBrowserLog:null,onLog:()=>undefined}});
  await page.setViewport({{width:320,height:180,deviceScaleFactor:1}});
  for(const kind of ['sc','jp','tofu']) {{
    await page.goto({{url:base,timeout:10000}});
    await page.evaluate(async (url)=>{{
      document.fonts.add(await new FontFace('Reference',`url(${{url}})`,{{weight:'700'}}).load());
      document.body.innerHTML='<div style="font-family:Reference;font-weight:700;font-size:64px;color:white;position:absolute;top:40px;left:10px">关复</div>';
      await document.fonts.ready;
    }},base+'/'+kind+'.otf');
    const shot=await page._client().send('Page.captureScreenshot',{{format:'png',fromSurface:true}});
    await fs.writeFile(root+'/'+kind+'.png',Buffer.from(shot.value.data,'base64'));
  }}
}} finally {{
  if(browser) await browser.close({{silent:true}});
  server.closeAllConnections();
  await new Promise(resolve=>server.close(resolve));
}}
''')
    process = subprocess.Popen(["node", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, errors = process.communicate(timeout=120)
        assert process.returncode == 0, errors.decode()
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    return {name: Image.open(root / f"{name}.png").convert("RGB") for name in ("sc", "jp", "tofu")}


@needs_browser
@pytest.mark.parametrize("worker", ["overlay-worker.mjs", "worker.mjs"])
def test_real_render_with_jp_only_fonts_still_encodes(tmp_path, worker, glyph_references):
    """两种真实渲染都回退 SC，解码后的关/复匹配独立 SC 对照，且可区分日文字形与方块。"""
    source = tmp_path / "japanese.ttf"
    source.write_bytes(sc_test_font("Noto Sans CJK JP"))
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", browser_executable=Path(BROWSER),
                        font_regular=source, font_bold=source, overlay_render_timeout_seconds=120)
    code = '''import React from "react";
export default function Text() { return <div style={{fontFamily: '"Noto Sans CJK SC", sans-serif', fontWeight:700,
fontSize:64, color:"white", position:"absolute", top:40, left:10}}>关复</div>; }'''
    renderer = Renderer(settings)
    directory = tmp_path / "render"
    if worker == "overlay-worker.mjs":
        overlay = Overlay(320, 180, 30, 3, [{"code": code, "config": {}, "start_frame": 0, "duration_frames": 3}])
        output = asyncio.run(render_overlay(renderer, overlay, directory))
    else:
        prepare_fonts(settings, directory)
        (directory / ".tmp").mkdir()
        (directory / "request.json").write_text(json.dumps({
            "code": code, "config": {}, "frames": [0, 2], "probes": [], "keywords": [],
            "composition": {"width": 320, "height": 180, "fps": 30, "duration_in_frames": 3},
            "browser": renderer.worker_browser_path(),
        }))
        report = asyncio.run(renderer.run_worker(directory, worker=worker, timeout_seconds=120))
        assert all(check["status"] == "pass" for check in report["checks"]), report
        assert "managed_fonts" in {check["name"] for check in report["checks"]}
        output = directory / "preview.mp4"
    # Decode the encoded output; a file or metadata alone would not prove visible text survived fallback.
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, timeout=30, check=True)
    assert len(result.stdout) == 320 * 180 * 3
    assert sum(value > 128 for value in result.stdout) > 900
    # Remotion disables font antialiasing, while screenshots and VP9 alpha have different edge coverage.
    # Compare covered pixels, then focus on the strokes that distinguish the SC and JP references.
    coverage = [0] * 65 + [255] * 191
    decoded = Image.frombytes("RGB", (320, 180), result.stdout).convert("L").point(coverage)
    controls = {name: image.convert("L").point(coverage) for name, image in glyph_references.items()}
    regional_strokes = ImageChops.difference(controls["sc"], controls["jp"])
    differences = {name: ImageStat.Stat(ImageChops.difference(decoded, image), mask=regional_strokes).mean[0]
                   for name, image in controls.items()}
    # Both wrong controls satisfy the old brightness-only assertion; the actual SC image must be much closer.
    assert all(sum(value > 128 for value in glyph_references[name].tobytes()) > 900 for name in ("jp", "tofu"))
    assert ImageStat.Stat(ImageChops.difference(decoded, controls["sc"])).mean[0] < 1
    assert differences["sc"] < 64 and min(differences["jp"], differences["tofu"]) > 192, differences
    runtime = json.loads((directory / "renderer.json").read_text())["runtime"]
    assert runtime["font_700_sha256"] == hashlib.sha256(bundled_font(700).data).hexdigest()
    assert runtime["font_700_selection"] == "bundled"
