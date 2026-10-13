/** Isolated browser preview: synchronize background video and accepted typography through one Player timeline. */
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Player, type PlayerRef } from "@remotion/player";
import { AbsoluteFill, Html5Video } from "remotion";
import Template from "imv:template";
import initial from "imv:config";
import { HostLayer, type HostBox, type HostTransform } from "./host-layer";

const channel = window.location.hash.slice(1);
/** Sync mode (`?sync=1`): no controls or loop; the parent scrubs the Player with `imv-preview-sync` times and an optional clip length. */
const sync = new URLSearchParams(window.location.search).get("sync") === "1";
type JsonValue = null | string | number | boolean | JsonValue[] | {[key: string]: JsonValue};
type Values = Record<string, JsonValue>;
/** Reject non-JSON or unbounded message values before updating the isolated Player. */
function jsonValue(value: unknown, depth = 0): value is JsonValue {
 if (depth > 30) return false;
 if (value === null || typeof value === "string" || typeof value === "boolean") return true;
 if (typeof value === "number") return Number.isFinite(value);
 if (Array.isArray(value)) return value.length < 10000 && value.every(item => jsonValue(item, depth + 1));
 return typeof value === "object" && Object.values(value).every(item => jsonValue(item, depth + 1));
}
/** A plain JSON object, as opposed to an array or a scalar. */
function isObject(value: unknown): value is Values {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
/** Overlay a parameter snapshot on the defaults: objects merge key by key, arrays and scalars replace. */
function merge(base: Values, patch: Values): Values {
  const out: Values = { ...base };
  for (const [key, value] of Object.entries(patch))
    out[key] = isObject(out[key]) && isObject(value) ? merge(out[key] as Values, value) : value;
  return out;
}
/** A non-empty rectangle of finite numbers. */
function isBox(value: unknown): value is HostBox {
  return isObject(value) && ["x", "y", "width", "height"].every((key) => typeof value[key] === "number" && Number.isFinite(value[key]))
    && (value.width as number) > 0 && (value.height as number) > 0;
}
/** A transform whose present fields are all finite numbers; unknown fields are refused. */
function isTransform(value: unknown): value is HostTransform {
  return isObject(value) && Object.entries(value).every(([key, item]) =>
    ["x", "y", "scale", "rotation", "opacity"].includes(key) && typeof item === "number" && Number.isFinite(item));
}
/**
 * Defaults and content box the serving page injects (`#imv-preview-host`). A published Sprite and a version saved
 * without rebuilding its bundle start from these, so the bundle's own `imv:config` is only the fallback.
 */
function pageData(): { config: Values; box: HostBox } {
  const c = initial.composition;
  const canvas = { x: 0, y: 0, width: c.width, height: c.height };
  try {
    const data = JSON.parse(document.getElementById("imv-preview-host")?.textContent ?? "null");
    return {
      config: isObject(data?.config) && jsonValue(data.config) ? data.config : initial.config,
      box: isBox(data?.box) ? data.box : canvas,
    };
  } catch {
    return { config: initial.config, box: canvas };
  }
}
const page = pageData();
/** Match the server's code-point ranges whenever the operator edits sample subtitle copy. */
function literalRanges(text: string, keywords: string[]): [number, number][] {
  const chars = Array.from(text);
  const ranges: [number, number][] = [];
  for (const word of new Set(keywords)) {
    const needle = Array.from(word);
    if (!needle.length) continue;
    for (let start = 0; start <= chars.length - needle.length; start++)
      if (needle.every((char, index) => chars[start + index] === char))
        ranges.push([start, start + needle.length]);
  }
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: [number, number][] = [];
  for (const [start, end] of ranges) {
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}
/** A revision identifies one parameter/background update, independently of playback frames. */
interface PreviewProps {
  values: Values;
  transform: HostTransform;
  background: string;
  requestId: number;
}

/**
 * Send only protocol notifications; `sync` tells the parent whether this bundle understands scrub messages and
 * `host` that it accepts partial values and a placement transform.
 */
/** The longest length the bus renders a Sprite at; a longer sync request is ignored. */
const MAX_LENGTH_SECONDS = 120;

function notify(type: string, message?: string, requestId?: number) {
  window.parent.postMessage({ type, channel, message, requestId, sync, host: true }, "*");
}

/** Keep both layers on Remotion's frame clock; failed background media leaves the typography visible. */
function Composition({ values, transform, background, requestId }: PreviewProps) {
  const [failed, setFailed] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const [loaded, setLoaded] = useState("");
  const keywords: string[] = initial.keywords ?? [];
  const highlights = keywords.length
    ? { highlightRanges: literalRanges(String(values["0_text"] ?? ""), keywords) }
    : {};
  useEffect(() => {
    if (
      background &&
      failed !== background &&
      (loaded !== background || !video.current || video.current.readyState < 2)
    )
      return;
    // Two animation frames let React commit and the browser paint before unlocking the parent.
    let painted = 0;
    const first = requestAnimationFrame(() => {
      painted = requestAnimationFrame(() =>
        notify("imv-preview-rendered", undefined, requestId),
      );
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(painted);
    };
  }, [requestId, background, loaded, failed]);
  return (
    <AbsoluteFill>
      {background && failed !== background && (
        <Html5Video
          key={background}
          ref={video}
          src={background}
          onLoadedData={() => setLoaded(background)}
          muted
          loop
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
          onError={() => {
            setFailed(background);
            notify(
              "imv-preview-error",
              "背景视频加载失败，请检查直链和视频格式。",
              requestId,
            );
          }}
        />
      )}
      <HostLayer box={page.box} transform={transform}>
        <Template {...values} {...highlights} />
      </HostLayer>
    </AbsoluteFill>
  );
}

/** Accept parameter snapshots (merged over the defaults) and placement transforms only from this iframe's parent. */
function App() {
  const [props, setProps] = useState<PreviewProps>({
    values: page.config,
    transform: {},
    background: "",
    requestId: 0,
  });
  const player = useRef<PlayerRef>(null);
  const c = initial.composition;
  // The bus renders a Sprite at the final video's length; in sync mode the parent passes the clip's length and the
  // composed Sprite lays itself out against it, like the real render. A seek waits until a new length is applied.
  const [length, setLength] = useState(c.duration_in_frames);
  const lengthRef = useRef(length);
  const pendingSeek = useRef<number | null>(null);
  useEffect(() => {
    if (pendingSeek.current === null) return;
    player.current?.seekTo(Math.min(length - 1, pendingSeek.current));
    pendingSeek.current = null;
  }, [length]);
  useEffect(() => {
    function receive(event: MessageEvent) {
      const data = event.data;
      if (event.source !== window.parent || data?.channel !== channel) return;
      if (data?.type === "imv-preview-sync") {
        // Scrub to the parent's clock; frames are derived here so the parent needs no fps.
        if (!sync || typeof data.time !== "number" || !Number.isFinite(data.time)) return;
        const duration = data.duration;
        if (duration !== undefined && (typeof duration !== "number" || !(duration > 0) || duration > MAX_LENGTH_SECONDS)) return;
        const frames = duration === undefined ? lengthRef.current : Math.max(1, Math.round(duration * c.fps));
        const frame = Math.max(0, Math.round(data.time * c.fps));
        player.current?.pause();
        if (frames === lengthRef.current) player.current?.seekTo(Math.min(frames - 1, frame));
        else {
          lengthRef.current = frames;
          pendingSeek.current = frame;
          setLength(frames);
        }
        return;
      }
      if (data?.type !== "imv-preview-update") return;
      const values = data.values;
      if (
        !values ||
        Array.isArray(values) ||
        !jsonValue(values) || JSON.stringify(values).length > 240000
      )
        return;
      if (
        typeof data.background !== "string" ||
        (data.background && !/^https?:\/\//i.test(data.background))
      )
        return;
      if (!Number.isSafeInteger(data.requestId) || data.requestId < 1) return;
      const transform = data.host?.transform ?? {};
      if (data.host !== undefined && (!isObject(data.host) || !isTransform(transform))) return;
      setProps({
        values: merge(page.config, values),
        transform,
        background: data.background,
        requestId: data.requestId,
      });
    }
    window.addEventListener("message", receive);
    notify("imv-preview-ready");
    return () => window.removeEventListener("message", receive);
  }, []);
  return (
    <Player
      ref={player}
      component={Composition}
      inputProps={props}
      compositionWidth={c.width}
      compositionHeight={c.height}
      durationInFrames={length}
      fps={c.fps}
      controls={!sync}
      loop={!sync}
      clickToPlay={!sync}
      doubleClickToFullscreen={!sync}
      spaceKeyToPlayOrPause={!sync}
      allowFullscreen={!sync}
      initiallyMuted
      style={{ width: "100%", height: "100%" }}
      errorFallback={() => <PreviewError requestId={props.requestId} />}
    />
  );
}

/** Surface render failures to the host so a crashed template cannot keep the controls locked. */
function PreviewError({ requestId }: { requestId: number }) {
  const message = "预览暂不可用，请恢复已确认参数后重试。";
  useEffect(() => notify("imv-preview-error", message, requestId), [requestId]);
  return <p role="alert">{message}</p>;
}

/** Await the same managed font files used by server rendering before exposing the Player. */
async function mount() {
  try {
    await Promise.all(
      [400, 700].map(async (weight) => {
        const url = new URL(`./fonts/${weight}`, window.location.href);
        const font = new FontFace("Noto Sans CJK SC", `url("${url}")`, {
          weight: String(weight),
        });
        document.fonts.add(await font.load());
      }),
    );
    createRoot(document.getElementById("root")!).render(<App />);
  } catch {
    document.getElementById("root")!.textContent = "预览字体加载失败，请重试。";
    notify("imv-preview-error", "预览字体加载失败，请重试。");
  }
}
void mount();
