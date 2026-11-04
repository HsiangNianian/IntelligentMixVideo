"""PR #309 regressions: font fallback evidence, atomic preview reads and changed bundled resources.

Run `uv run --locked --project server pytest server/tests/test_remotion_font_review.py -v`.
All font files and HTTP dependencies are temporary; no production services are contacted.
"""

import hashlib
import json
import lzma
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._f_v_a_r import Axis

from server.remotion_templates import managed_fonts, routes
from server.remotion_templates.managed_fonts import FAMILY, bundled_font
from server.remotion_templates.publication import PresentationBuilder
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings

from .test_remotion_managed_fonts import sc_test_font


@pytest.fixture
def preview(tmp_path):
    """Serve a version with a real configured SC font and a mutable, host-owned validation snapshot."""
    source = tmp_path / "regular.ttf"
    source.write_bytes(sc_test_font(FAMILY))
    settings = Settings(_env_file=None, data_dir=tmp_path / "state", font_regular=source, font_bold=source)
    runtime = {"font_400": hashlib.sha256(source.read_bytes()).hexdigest()}
    version = SimpleNamespace(validation=SimpleNamespace(runtime=runtime))
    service = SimpleNamespace(settings=settings, store=SimpleNamespace(version=lambda identifier: version))
    application = FastAPI()
    application.include_router(routes.router, prefix="/api/templates")
    application.dependency_overrides[routes.runtime] = lambda: service
    with TestClient(application) as client:
        yield client, f"/api/templates/versions/{uuid4()}/fonts/400", source, runtime


def test_version_preview_uses_the_browser_selected_fallback(preview):
    """A version whose worker selected bundled SC must serve those exact bytes instead of the valid primary face."""
    client, url, source, runtime = preview
    fallback = bundled_font(400)
    runtime.update(font_400_selection="bundled", font_400_sha256=hashlib.sha256(fallback.data).hexdigest())
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == fallback.data != source.read_bytes()
    assert response.headers["etag"] == f'"{runtime["font_400_sha256"]}"'


def test_preview_checks_and_serves_one_font_snapshot(preview, monkeypatch):
    """Replacing the configured file between validation and response must never serve unchecked new bytes."""
    client, url, source, _ = preview
    original = source.read_bytes()
    replacement = sc_test_font(FAMILY, advance=950)
    read = Path.read_bytes
    reads = []

    def replace_after_read(path):
        """Simulate an administrator replacing the font immediately after the endpoint's first read."""
        data = read(path)
        if path == source:
            reads.append(data)
            source.write_bytes(replacement)
        return data

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == original
    assert reads == [original]


def test_system_font_endpoint_falls_back_when_configuration_is_missing(preview):
    """The default catalog endpoint still serves a usable Chinese font when the system TTC was removed."""
    client, _, source, _ = preview
    source.unlink()
    response = client.get("/api/templates/fonts/noto-sans-cjk")
    assert response.status_code == 200
    assert response.content == bundled_font(400).data
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.fixture
def bundled_resources(tmp_path, monkeypatch):
    """Install a small, valid pinned variable font in an isolated package directory and restore legacy caches."""
    root = tmp_path / "package"
    root.mkdir()
    (root / "bundled_fonts").mkdir()
    monkeypatch.setattr(managed_fonts, "__file__", str(root / "managed_fonts.py"))
    # The reviewed implementation has a weight-only cache; clear prior tests' entries before reproducing its staleness.
    clear = getattr(bundled_font, "cache_clear", lambda: None)
    clear()

    def write(advance):
        """Replace both the bundled font and its catalog pin as a package update would."""
        with TTFont(BytesIO(sc_test_font("Noto Sans SC", advance=advance))) as font:
            axis = Axis()
            axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue = "wght", 100, 400, 900
            axis.flags, axis.axisNameID = 0, 256
            font["fvar"] = newTable("fvar")
            font["fvar"].axes, font["fvar"].instances = [axis], []
            output = BytesIO()
            font.save(output)
        data = output.getvalue()
        (root / "bundled_fonts/NotoSansSC-VF.ttf.xz").write_bytes(lzma.compress(data))
        (root / "font_catalog.json").write_text(json.dumps([{
            "family": "Noto Sans SC", "filename": "NotoSansSC-VF.ttf",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }]))
        return data

    write(600)
    yield root, write
    clear()


def test_fallback_resource_update_changes_publication_evidence(bundled_resources, tmp_path):
    """An unchanged bad primary source must not hide a different bundled face behind its source fingerprint."""
    _, write = bundled_resources
    source = tmp_path / "jp.ttf"
    source.write_bytes(sc_test_font("Noto Sans CJK JP"))
    settings = Settings(_env_file=None, font_regular=source, font_bold=source)
    builder = PresentationBuilder(Renderer(settings))
    before = builder.environment()
    updated = write(900)
    after = builder.environment()
    assert before != after
    assert after["font_400_sha256"] == hashlib.sha256(updated).hexdigest()


def test_cached_bundled_font_does_not_hide_tampering(bundled_resources):
    """A cached fallback cannot bypass the catalog's hash check when its compressed resource changes."""
    root, _ = bundled_resources
    bundled_font(400)
    (root / "bundled_fonts/NotoSansSC-VF.ttf.xz").write_bytes(lzma.compress(b"different font"))
    with pytest.raises(ValueError, match="catalog"):
        bundled_font(400)


@pytest.mark.parametrize("worker", ["worker.mjs", "overlay-worker.mjs"])
@pytest.mark.parametrize("failure", ["primary", "both", "timeout"])
def test_render_entrypoint_tries_bundled_sc_before_local_fallback(tmp_path, worker, failure):
    """Execute each real generated entrypoint: primary failure tries bundled SC once and all failures still continue."""
    if not shutil.which("node"):
        pytest.skip("requires Node")
    renderer = Path(__file__).parents[1] / "src/server/remotion"
    if not (renderer / "node_modules/typescript").is_dir():
        pytest.skip("requires locked renderer dependencies")
    (tmp_path / "node_modules").symlink_to(renderer / "node_modules", target_is_directory=True)
    work = tmp_path / "work"
    work.mkdir()
    stub = tmp_path / "stub.mjs"
    stub.write_text('''/** Execute the trusted entrypoint with failing font I/O; no generated component or encoder is run. */
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
export {recordFontLoad} from '__FONT_CHECK__';
export const VERSION = 'fixture';
export const languageDiagnostics = () => ({diagnostics:[]});
export const exportTemplate = async code => code;
export const buildPresentation = async () => {};
export const openBrowser = async () => ({close:async()=>{}});
export const verifyManagedFonts = async () => ({fallbackWeights:[],runtime:{font_fallback_sha256:'fallback-hash'}});
const fontLogs=[];
export const selectComposition = async ({onBrowserLog}) => {fontLogs.forEach(log=>onBrowserLog?.(log));return {id:'fixture'};};
export const renderStill = async ({onBrowserLog}) => {fontLogs.forEach(log=>onBrowserLog?.(log));};
export const renderMedia = async ({onBrowserLog}) => {fontLogs.forEach(log=>onBrowserLog?.(log));};
export async function bundle({entryPoint}) {
  const attempts = [], loaded = [];
  let resume;
  const continued = new Promise(resolve => {resume=resolve;});
  const context = {exports:{}, console:{...console,info:text=>fontLogs.push({text})}, Promise,
    setTimeout:(fn,delay)=>setTimeout(fn, Math.min(delay, 20)), clearTimeout,
    document:{documentElement:{style:{}}, fonts:{add:face=>loaded.push(face.source)}},
    FontFace:class {
      constructor(family,source){this.source=source;}
      load(){
        attempts.push(this.source);
        if(process.env.FONT_FAILURE==='timeout' && !this.source.includes('fallback')) return new Promise(()=>{});
        return process.env.FONT_FAILURE!=='both' && this.source.includes('fallback') ? Promise.resolve(this) : Promise.reject(new Error('OTS rejected font'));
      }
    },
    require(name){
      if(name==='remotion') return {delayRender:()=>1,continueRender:resume,staticFile:name=>name,registerRoot:()=>{}};
      if(name.endsWith('font-loader.mjs')) {
        const child={...context,exports:{}};
        vm.runInNewContext(ts.transpile(fs.readFileSync(process.env.IMV_RENDERER_ROOT+'/font-loader.mjs','utf8'),{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}),child);
        return child.exports;
      }
      return {};
    },
  };
  vm.runInNewContext(ts.transpile(fs.readFileSync(entryPoint,'utf8'),{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}),context);
  await Promise.race([continued,new Promise((_,reject)=>setTimeout(()=>reject(new Error('render never continued')),1000))]);
  fs.writeFileSync(process.env.IMV_WORK_ROOT+'/font-result.json',JSON.stringify({attempts,loaded,continued:true}));
  return 'fixture';
}
'''.replace('__FONT_CHECK__', (renderer / "managed-fonts.mjs").as_uri()))
    source = (renderer / worker).read_text()
    for dependency in ["@remotion/bundler", "@remotion/renderer", "remotion", "./managed-fonts.mjs", "./typescript.mjs", "./presentation.mjs"]:
        source = source.replace(json.dumps(dependency), json.dumps(stub.as_uri()))
    (tmp_path / "test-worker.mjs").write_text(source)
    (work / "request.json").write_text(json.dumps({
        "code": 'export default function Text() { return null; }', "config": {}, "frames": [0], "probes": [],
        "composition": {"width": 320, "height": 180, "fps": 30, "duration_in_frames": 1, "frames": 1}, "instances": [],
    }))
    process = subprocess.run(["node", str(tmp_path / "test-worker.mjs")], capture_output=True, text=True, timeout=20,
                             env={**os.environ, "IMV_WORK_ROOT": str(work), "IMV_RENDERER_ROOT": str(renderer), "FONT_FAILURE": failure})
    assert process.returncode == 0, process.stderr
    assert (work / "font-result.json").is_file(), (work / "renderer.json").read_text()
    result = json.loads((work / "font-result.json").read_text())
    assert result["continued"] is True
    assert len(result["attempts"]) == 4
    assert sum("fallback" in url for url in result["attempts"]) == 2
    assert len(result["loaded"]) == (0 if failure == "both" else 2)
    runtime = json.loads((work / "renderer.json").read_text())["runtime"]
    assert runtime["font_400_load_fallback"] == ("local" if failure == "both" else "bundled")
    if failure != "both":
        assert runtime["font_400_selection"] == "bundled" and runtime["font_400_sha256"] == "fallback-hash"
