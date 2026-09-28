/** Transparent, sandboxed Sprite player controlled by the IMS preview clock through postMessage. */
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Player, type PlayerRef } from "@remotion/player";
import { AbsoluteFill, Freeze } from "remotion";
import Template from "imv:template";
import initial from "imv:config";

const channel = window.location.hash.slice(1);
type Scalar = string | number | boolean;
type Values = Record<string, Scalar | number[][]>;
type Control = { key: string; access: number; defaultValue: Scalar; minimum?: number; maximum?: number };
type PreviewConfig = {
  config: Record<string, Scalar>;
  composition: { width: number; height: number; fps: number; duration_in_frames: number };
  textProp: string;
  keywordsProp: string;
  parameters: Control[];
  animationFrames: number;
  staticFrame: number;
};
const published = initial as PreviewConfig;

/** Reply only to the parent that owns this unguessable iframe channel. */
function notify(type: string, requestId?: number, message?: string) {
  window.parent.postMessage({ type, channel, requestId, message }, "*");
}

/** Match the server's literal Unicode code-point ranges, merging every overlap. */
function literalRanges(text: string, keywords: string[]): number[][] {
  const chars = Array.from(text);
  const matches: number[][] = [];
  for (const keyword of new Set(keywords)) {
    const needle = Array.from(keyword);
    if (!needle.length) continue;
    for (let start = 0; start <= chars.length - needle.length; start++)
      if (needle.every((char, index) => chars[start + index] === char))
        matches.push([start, start + needle.length]);
  }
  matches.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: number[][] = [];
  for (const [start, end] of matches) {
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

/** Keep arbitrary parent messages out of the accepted component's scalar props. */
function checkedValues(data: any): { values: Values; totalFrames: number; freezeFrame: number | null } | null {
  if (!Number.isSafeInteger(data.totalFrames) || data.totalFrames < 1 || data.totalFrames > 216000)
    return null;
  if (typeof data.text !== "string" || data.text.length > 2000)
    return null;
  if (!Array.isArray(data.keywords) || data.keywords.length > 20 ||
      data.keywords.some((word: unknown) => typeof word !== "string" || !word || word.length > 100))
    return null;
  if ((!published.textProp && data.text) || (!published.keywordsProp && data.keywords.length))
    return null;
  if (!data.overrides || typeof data.overrides !== "object" || Array.isArray(data.overrides))
    return null;
  const values: Values = { ...published.config };
  const controls = new Map(published.parameters.map((item) => [item.key, item]));
  for (const [key, value] of Object.entries(data.overrides)) {
    const control = controls.get(key);
    if (!control || control.access !== 2 || typeof value !== typeof control.defaultValue ||
        (typeof value === "number" && (!Number.isFinite(value) ||
          (control.minimum !== undefined && value < control.minimum) ||
          (control.maximum !== undefined && value > control.maximum))))
      return null;
    values[key] = value as Scalar;
  }
  if (published.textProp) values[published.textProp] = data.text;
  if (published.keywordsProp)
    values[published.keywordsProp] = literalRanges(data.text, data.keywords);
  return {
    values,
    totalFrames: data.totalFrames,
    freezeFrame: published.keywordsProp && published.animationFrames > data.totalFrames
      ? published.staticFrame : null,
  };
}

/** Render the accepted Sprite without a background; short subtitles freeze the accepted visible frame. */
function Composition({ values, freezeFrame }: { values: Values; freezeFrame: number | null }) {
  return <AbsoluteFill>{freezeFrame === null
    ? <Template {...values} />
    : <Freeze frame={freezeFrame}><Template {...values} /></Freeze>}
  </AbsoluteFill>;
}

/** Apply style changes on React commits and seek each frame directly from the IMS clock. */
function App() {
  const player = useRef<PlayerRef>(null);
  const pendingFrame = useRef(0);
  const totalFrames = useRef(published.composition.duration_in_frames);
  const [view, setView] = useState({
    values: published.config as Values,
    totalFrames: totalFrames.current,
    freezeFrame: null as number | null,
    requestId: 0,
  });
  useEffect(() => {
    /** Accept configuration and frame messages only from this iframe's parent. */
    function receive(event: MessageEvent) {
      const data = event.data;
      if (event.source !== window.parent || data?.channel !== channel) return;
      if (data.type === "imv-sprite-hello") {
        notify("imv-sprite-ready");
      } else if (data.type === "imv-sprite-seek") {
        if (!Number.isSafeInteger(data.frame) || data.frame < 0) return;
        pendingFrame.current = data.frame;
        player.current?.seekTo(Math.min(data.frame, totalFrames.current - 1));
      } else if (data.type === "imv-sprite-config") {
        if (!Number.isSafeInteger(data.requestId) || data.requestId < 1) return;
        const checked = checkedValues(data);
        if (!checked) {
          notify("imv-sprite-error", data.requestId, "Sprite 预览参数无效");
          return;
        }
        totalFrames.current = checked.totalFrames;
        setView({ ...checked, requestId: data.requestId });
      }
    }
    window.addEventListener("message", receive);
    notify("imv-sprite-ready");
    return () => window.removeEventListener("message", receive);
  }, []);
  useEffect(() => {
    player.current?.seekTo(Math.min(pendingFrame.current, view.totalFrames - 1));
    // Hidden iframes may suspend animation frames, so acknowledge after the React commit.
    if (view.requestId) notify("imv-sprite-rendered", view.requestId);
  }, [view]);
  return <Player
    ref={player}
    component={Composition}
    inputProps={{ values: view.values, freezeFrame: view.freezeFrame }}
    compositionWidth={published.composition.width}
    compositionHeight={published.composition.height}
    fps={published.composition.fps}
    durationInFrames={view.totalFrames}
    controls={false}
    initiallyMuted
    style={{ width: "100%", height: "100%", background: "transparent" }}
    errorFallback={() => <PreviewError requestId={view.requestId} />}
  />;
}

/** Report a rendering failure so the editor does not silently show an empty overlay. */
function PreviewError({ requestId }: { requestId: number }) {
  useEffect(() => notify("imv-sprite-error", requestId, "Sprite 预览渲染失败"), [requestId]);
  return <p role="alert">Sprite 预览失败</p>;
}

/** Load the same managed font weights before presenting the published component. */
async function mount() {
  try {
    await Promise.all([400, 700].map(async (weight) => {
      const font = new FontFace("Noto Sans CJK SC", `url("${new URL(`./fonts/${weight}`, window.location.href)}")`, {
        weight: String(weight),
      });
      document.fonts.add(await font.load());
    }));
    createRoot(document.getElementById("root")!).render(<App />);
  } catch {
    notify("imv-sprite-error", undefined, "Sprite 预览字体加载失败");
  }
}
void mount();
