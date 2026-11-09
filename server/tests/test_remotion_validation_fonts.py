"""Validation font regressions: HTTP failures, diagnostic ordering and bounded host readiness.

Run with `uv run --locked --project server pytest server/tests/test_remotion_validation_fonts.py -v`.
Node executes the actual worker functions/host with isolated I/O; no model or production service is used.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


RENDERER = Path(__file__).parents[1] / "src/server/remotion"
pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (RENDERER / "node_modules/typescript").is_dir(),
    reason="requires Node and locked renderer dependencies",
)


def run_node(tmp_path: Path, source: str) -> None:
    """Run trusted regression code against the real source with a bounded lifetime and isolated files."""
    (tmp_path / "node_modules").symlink_to(RENDERER / "node_modules", target_is_directory=True)
    script = tmp_path / "test.mjs"
    script.write_text(source, encoding="utf-8")
    process = subprocess.run(
        ["node", str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={**os.environ, "IMV_RENDERER_ROOT": str(RENDERER)},
    )
    assert process.returncode == 0, process.stdout + process.stderr


# Load actual function declarations without starting the worker's top-level job or browser.
WORKER_FUNCTIONS = r"""
import fs from 'node:fs/promises';
import http from 'node:http';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source = await fs.readFile(process.env.IMV_RENDERER_ROOT + '/tool-validation-worker.mjs', 'utf8');
const parsed = ts.createSourceFile('worker.mjs', source, ts.ScriptTarget.Latest, true);
const functions = parsed.statements.filter(ts.isFunctionDeclaration).map(node => node.getText(parsed)).join('\n');
"""


@pytest.mark.parametrize("state", ["missing", "unreadable", "valid"])
def test_validation_font_http_response(tmp_path, state):
    """Missing/unreadable fonts return 404 without crashing; valid fonts return their exact bytes with 200."""
    public = tmp_path / "public"
    public.mkdir()
    target = public / "font-400.otf"
    if state == "valid":
        target.write_bytes(b"test-font")
    elif state == "unreadable":
        target.mkdir()
    run_node(tmp_path, WORKER_FUNCTIONS + """
const context = {fs, http, root: process.cwd()};
vm.createContext(context);
vm.runInContext(functions, context);
const {server, url} = await context.serveBundle();
try {
  const response = await fetch(url + 'font-400.otf');
  assert.equal(response.status, EXPECTED_STATUS);
  assert.equal(await response.text(), EXPECTED_BODY);
  assert.equal((await fetch(url)).status, 200, 'the server remains usable');
} finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
""".replace("EXPECTED_STATUS", "200" if state == "valid" else "404")
        .replace("EXPECTED_BODY", json.dumps("test-font" if state == "valid" else "")))


@pytest.mark.parametrize("mode", ["fit", "capacity"])
def test_validation_diagnoses_fonts_before_loading_any_sprite(tmp_path, mode):
    """Diagnostic replacement finishes on the initial page, once, before either Sprite loads the selected files."""
    run_node(tmp_path, WORKER_FUNCTIONS + """
const events = [];
const page = {__imvUrl:'host', goto:async()=>events.push('host'), evaluate:async()=>null};
const context = {fs, root:process.cwd(), rendererRoot:process.env.IMV_RENDERER_ROOT,
  request:{mode:MODE, composition:{width:100,height:100}, sprites:[{code:''},{code:''}]}, result:{},
  checkManagedFonts:async()=>events.push('diagnostic'),
};
vm.createContext(context);
vm.runInContext(functions, context);
Object.assign(context, {
  serveBundle:async()=>({server:{close:callback=>callback()},url:'host'}),
  openPage:async()=>({page,browser:{close:async()=>{}}}),
  sourcePolicy:()=>{}, bundleHost:async()=>{}, fitTexts:async()=>[], textCapacities:async()=>[],
});
await context.fitAll();
assert.deepEqual(events, ['diagnostic','host','host']);
""".replace("MODE", json.dumps(mode)))


def test_validation_host_waits_for_managed_font_loading(tmp_path):
    """Even an already-resolved document.fonts.ready cannot expose a component before the explicit loader settles."""
    run_node(tmp_path, """
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import ts from 'typescript';
let finish, mounted = false;
const loading = new Promise(resolve => { finish = resolve; });
const context = {exports:{}, window:{addEventListener(){}},
  document:{getElementById:()=>({}),fonts:{ready:Promise.resolve()}},
  require(name) {
    if (name === 'react') return {__esModule:true,default:{Component:class {}}};
    if (name === 'react/jsx-runtime') return {jsx:()=>({})};
    if (name === 'react-dom/client') return {createRoot:()=>({render:()=>{mounted=true;}})};
    if (name === 'imv:config') return {__esModule:true,default:{composition:{width:100,height:100},parameter_schema:{}}};
    if (name === 'ajv/dist/2020.js') return {__esModule:true,default:class {compile(){return ()=>true;}}};
    if (name === './font-loader.mjs') return {loadManagedFonts:()=>loading};
    return {};
  },
};
const host = await fs.readFile(process.env.IMV_RENDERER_ROOT + '/tool-validation-host.tsx','utf8');
vm.runInNewContext(ts.transpile(host,{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}), context);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(mounted, false, 'component mounted while managed fonts were still loading');
finish({400:'local',700:'local'});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(mounted, true, 'local fallback must still release the host');
""")
