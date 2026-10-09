/**
 * Browser host for PR76 behavior validation.  It mounts one accepted component
 * in a fixed-size Remotion Player and exposes only frame/parameter operations
 * and DOM snapshots to the worker's test-context bridge.
 */
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AbsoluteFill } from "remotion";
import { Player, type PlayerRef } from "@remotion/player";
import Template from "imv:template";
import initial from "imv:config";
import Ajv2020 from "ajv/dist/2020.js";

type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject;
type JsonObject = { [key: string]: JsonValue };
type Config = {
  defaults: JsonObject;
  configured: JsonObject;
  parameter_schema: JsonObject;
  composition: { width: number; height: number; fps: number; duration_frames: number };
};
type Snapshot = {
  text: string;
  box: { x: number; y: number; width: number; height: number };
  styles: Record<string, string>;
};

const config = initial as Config;
const parameterValidator = new Ajv2020({ strict: false, allErrors: true, validateFormats: false }).compile(config.parameter_schema);

// Keep component exceptions observable to the worker without exposing browser
// globals through the TestContext API.
window.addEventListener("error", (event) => {
  (window as any).__imvRuntimeError = String(event.error?.message ?? event.message ?? "Runtime error");
});
window.addEventListener("unhandledrejection", (event) => {
  (window as any).__imvRuntimeError = String((event.reason as any)?.message ?? event.reason ?? "Unhandled rejection");
});

/** Merge object patches recursively while replacing arrays and scalar values. */
function mergeValues(base: JsonValue, patch: JsonValue): JsonValue {
  if (isObject(base) && isObject(patch)) {
    const out: JsonObject = { ...base };
    for (const [key, value] of Object.entries(patch))
      out[key] = Object.hasOwn(base, key) ? mergeValues(base[key], value) : value;
    return out;
  }
  return patch;
}

function isObject(value: JsonValue): value is JsonObject {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

/** Wait for browser-managed fonts and media elements before exposing a frame. */
async function waitResources(): Promise<void> {
  await document.fonts?.ready;
  const end = Date.now() + 5000;
  while (Date.now() < end) {
    const media = Array.from(document.querySelectorAll("img,video,audio"));
    if (media.every((item) => {
      if (item instanceof HTMLImageElement) return item.complete;
      if (item instanceof HTMLMediaElement) return item.readyState >= 2 || item.error !== null;
      return true;
    })) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

/** Return DOM snapshots relative to the fixed validation canvas. */
function querySnapshot(selector: string, all: boolean): Snapshot[] {
  const root = document.querySelector("[data-imv-validation-canvas]");
  if (!root) throw new Error("Validation canvas is unavailable");
  const nodes = all ? Array.from(root.querySelectorAll(selector)) : [root.querySelector(selector)].filter(Boolean) as Element[];
  const canvasBox = root.getBoundingClientRect();
  return nodes.map((node) => {
    const box = node.getBoundingClientRect();
    const styles = getComputedStyle(node);
    const styleValues: Record<string, string> = {};
    for (const name of Array.from(styles)) styleValues[name] = styles.getPropertyValue(name);
    return { text: node.textContent ?? "", box: { x: box.x - canvasBox.x, y: box.y - canvasBox.y, width: box.width, height: box.height }, styles: styleValues };
  });
}

/** Mount the Player and publish a narrow host bridge for the isolated worker. */
function App() {
  const player = useRef<PlayerRef>(null);
  const [values, setValues] = useState<JsonObject>(config.defaults);
  const [mountVersion, setMountVersion] = useState(0);
  const [ready, setReady] = useState(false);
  const valuesRef = useRef(values);
  valuesRef.current = values;
  useEffect(() => {
    const tool = {
      composition: Object.freeze({ ...config.composition }),
      ready: false,
      async setFrame(frame: number) {
        if (!Number.isSafeInteger(frame) || frame < 0 || frame >= config.composition.duration_frames) throw new Error("frame is outside composition");
        player.current?.seekTo(frame);
        await nextFrame();
        await waitResources();
      },
      async setParameters(patch: JsonObject) {
        const merged = mergeValues(valuesRef.current, patch);
        if (!parameterValidator(merged)) throw new Error(JSON.stringify(parameterValidator.errors));
        setValues(merged as JsonObject);
        await nextFrame();
        await waitResources();
      },
      async reset() {
        delete (window as any).__imvRuntimeError;
        setValues(config.configured);
        setMountVersion((version) => version + 1);
        await nextFrame();
        await waitResources();
      },
      /** Execute one test in a dedicated opaque Worker; DOM and assertions stay in this page. */
      async runTest(source: string, timeoutMs: number) {
        const assertions: unknown[] = [];
        const workerCode = `
          self.onmessage = async (event) => {
            const pending = new Map(); let nextId = 0;
            const rpc = (op, args) => new Promise((resolve, reject) => { const id = ++nextId; pending.set(id, {resolve, reject}); self.postMessage({kind:'request', id, op, args}); });
            self.onmessage = async (message) => {
              const value = message.data;
              if (value.kind === 'response') { const item = pending.get(value.id); if (!item) return; pending.delete(value.id); value.error ? item.reject(new Error(value.error)) : item.resolve(value.value); }
            };
            const emit = (message, passed, actual = null, expected = null) => self.postMessage({kind:'assertion', value:{message:String(message), passed:Boolean(passed), actual, expected}});
            const equal = (a,b) => { if (Object.is(a,b)) return true; if (Array.isArray(a)||Array.isArray(b)) return Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>equal(v,b[i])); if (a&&typeof a==='object'&&b&&typeof b==='object'){const ak=Object.keys(a).sort(),bk=Object.keys(b).sort();return ak.length===bk.length&&ak.every((k,i)=>k===bk[i]&&equal(a[k],b[k]));} return false; };
            const ctx = Object.freeze({
              composition: Object.freeze(event.data.composition),
              set_frame: (frame) => rpc('setFrame', frame),
              set_parameters: (patch) => rpc('setParameters', patch),
              query: (selector) => rpc('query', selector),
              query_all: (selector) => rpc('queryAll', selector),
              assert(condition, message) { const passed=Boolean(condition); emit(message, passed, passed, true); if (!passed) throw new Error(String(message)); },
              assert_equal(actual, expected, message) { const passed=equal(actual,expected); emit(message,passed,actual,expected); if (!passed) throw new Error(String(message)); },
              assert_close(actual, expected, tolerance, message) { const passed=Number.isFinite(actual)&&Number.isFinite(expected)&&Number.isFinite(tolerance)&&tolerance>=0&&Math.abs(actual-expected)<=tolerance; emit(message,passed,actual,expected); if (!passed) throw new Error(String(message)); },
            });
            let error = null;
            try { const fn = new Function(${JSON.stringify(source)} + '\\n;return __imvTest;')(); if (typeof fn !== 'function') throw new Error('Default export is not a function'); await fn(ctx); }
            catch (caught) { error = String(caught?.message ?? caught); }
            self.postMessage({kind:'done', error});
          };
        `;
        const worker = new Worker(URL.createObjectURL(new Blob([workerCode], { type: "text/javascript" })));
        return await new Promise((resolve) => {
          let settled = false;
          const finish = (value: unknown) => { if (settled) return; settled = true; clearTimeout(timer); worker.terminate(); resolve(value); };
          const timer = window.setTimeout(() => finish({ assertions, error: "Test script timed out", runtimeError: null }), timeoutMs);
          worker.onmessage = async (event) => {
            const value = event.data;
            if (value.kind === "assertion") { assertions.push(value.value); return; }
            if (value.kind === "request") {
              try {
                const output = value.op === "setFrame" ? await tool.setFrame(value.args) : value.op === "setParameters" ? await tool.setParameters(value.args) : value.op === "queryAll" ? querySnapshot(value.args, true) : querySnapshot(value.args, false)[0] ?? null;
                worker.postMessage({ kind: "response", id: value.id, value: output });
              } catch (error) { worker.postMessage({ kind: "response", id: value.id, error: String((error as Error)?.message ?? error) }); }
              return;
            }
            if (value.kind === "done") finish({ assertions, error: value.error, runtimeError: (window as any).__imvRuntimeError ?? null });
          };
          worker.onerror = (error) => finish({ assertions, error: String(error.message ?? error), runtimeError: null });
          worker.postMessage({ source, timeoutMs, composition: config.composition });
        });
      },
    };
    (window as any).__imvTool = tool;
    setReady(true);
    tool.ready = true;
    return () => { delete (window as any).__imvTool; };
  }, []);
  return <div data-imv-validation-ready={ready ? "true" : "false"} style={{ width: config.composition.width, height: config.composition.height }}>
      <Player
      key={mountVersion}
      ref={player}
      component={Composition}
      inputProps={{ values }}
      compositionWidth={config.composition.width}
      compositionHeight={config.composition.height}
      durationInFrames={config.composition.duration_frames}
      fps={config.composition.fps}
      initiallyPaused
      controls={false}
      style={{ width: config.composition.width, height: config.composition.height }}
    />
  </div>;
}

function Composition({ values }: { values: JsonObject }) {
  return <AbsoluteFill data-imv-validation-canvas><RuntimeBoundary><Template {...values} /></RuntimeBoundary></AbsoluteFill>;
}

/** Convert render-time component exceptions into an observable validation error. */
class RuntimeBoundary extends React.Component<React.PropsWithChildren<{}>, { failed: boolean }> {
  state = { failed: false };
  componentDidCatch(error: Error) {
    (window as any).__imvRuntimeError = error.message;
    this.setState({ failed: true });
  }
  render() { return this.state.failed ? <div data-imv-validation-error="true" /> : this.props.children; }
}

createRoot(document.getElementById("root")!).render(<App />);

export type { Snapshot };
