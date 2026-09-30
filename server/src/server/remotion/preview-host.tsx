/** Isolated browser preview: synchronize background video and accepted typography through one Player timeline. */
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Player } from "@remotion/player";
import { AbsoluteFill, Html5Video } from "remotion";
import Template from "imv:template";
import initial from "imv:config";

const channel = window.location.hash.slice(1);
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
  background: string;
  requestId: number;
}

/** Send only protocol notifications; the parent must check source, channel and message type. */
function notify(type: string, message?: string, requestId?: number) {
  window.parent.postMessage({ type, channel, message, requestId }, "*");
}

/** Keep both layers on Remotion's frame clock; failed background media leaves the typography visible. */
function Composition({ values, background, requestId }: PreviewProps) {
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
      <AbsoluteFill>
        <Template {...values} {...highlights} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
}

/** Accept complete scalar parameter snapshots only from this iframe's parent. */
function App() {
  const [props, setProps] = useState<PreviewProps>({
    values: initial.config,
    background: "",
    requestId: 0,
  });
  useEffect(() => {
    function receive(event: MessageEvent) {
      const data = event.data;
      if (
        event.source !== window.parent ||
        data?.channel !== channel ||
        data?.type !== "imv-preview-update"
      )
        return;
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
      setProps({
        values,
        background: data.background,
        requestId: data.requestId,
      });
    }
    window.addEventListener("message", receive);
    notify("imv-preview-ready");
    return () => window.removeEventListener("message", receive);
  }, []);
  const c = initial.composition;
  return (
    <Player
      component={Composition}
      inputProps={props}
      compositionWidth={c.width}
      compositionHeight={c.height}
      durationInFrames={c.duration_in_frames}
      fps={c.fps}
      controls
      loop
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
