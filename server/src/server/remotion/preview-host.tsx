/** Isolated browser preview: synchronize background video and accepted typography through one Player timeline. */
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Player } from "@remotion/player";
import { AbsoluteFill, Html5Video } from "remotion";
import Template from "imv:template";
import initial from "imv:config";

const channel = window.location.hash.slice(1);
type Values = Record<string, string | number | boolean>;
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
        <Template {...values} />
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
        Object.keys(values).length !== Object.keys(initial.config).length ||
        !Object.entries(initial.config).every(
          ([key, value]) =>
            Object.hasOwn(values, key) &&
            typeof values[key] === typeof value &&
            (typeof values[key] !== "number" || Number.isFinite(values[key])),
        )
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
