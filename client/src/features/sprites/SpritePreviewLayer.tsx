/** Overlay sandboxed published Sprite players on the IMS preview and drive them from its frame clock. */
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { apiBase } from "@/lib/api-base";
import { SpriteKind, type SpritePlacement, type SpriteSummary } from "@/generated/imv/sprite/v1/sprite_pb";
import { previewWindow, sampleCopy, type SpritePreviewCopy } from "./preview";

export interface SpritePreviewLayerHandle {
  seek(time: number): void;
}

interface ItemHandle {
  seek(time: number): void;
}

/** Keep a single Sprite mounted across timeline seeks; only the trusted iframe runs its TSX. */
function SpritePreviewItem({
  ref,
  placement,
  sprite,
  duration,
  canvas,
  copy,
  order,
}: {
  ref: Ref<ItemHandle>;
  placement: SpritePlacement;
  sprite: SpriteSummary;
  duration: number;
  canvas: { width: number; height: number };
  copy?: SpritePreviewCopy;
  order: number;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [channel] = useState(() => crypto.randomUUID());
  const ready = useRef(false);
  const configured = useRef(false);
  const latestTime = useRef(0);
  const lastFrame = useRef(-1);
  const requestId = useRef(0);
  const [active, setActive] = useState(false);
  const [status, setStatus] = useState("正在准备 Sprite 预览…");
  const range = previewWindow(placement, duration);
  const fps = sprite.canvas?.fps ?? 0;
  const startFrame = range ? Math.round(range.start * fps) : 0;
  const totalFrames = range ? Math.round(range.end * fps) - startFrame : 0;
  const example = copy ?? sampleCopy(placement.target);
  const text = sprite.kind === SpriteKind.TEXT ? example.text : "";
  const keywords = sprite.keywordsSupported && sprite.kind === SpriteKind.TEXT
    ? example.keywords.split(/[\s,，、]+/).filter(Boolean).slice(0, 20)
    : [];
  const overrides = Object.fromEntries(placement.overrides.flatMap((item) =>
    item.value?.value.case ? [[item.key, item.value.value.value]] : [],
  ));
  const configKey = JSON.stringify({ text, keywords, overrides, totalFrames });
  const scale = sprite.canvas && canvas.width && canvas.height
    ? Math.max(canvas.width / sprite.canvas.width, canvas.height / sprite.canvas.height)
    : 0;
  const width = sprite.canvas ? sprite.canvas.width * scale / canvas.width * 100 : 0;
  const height = sprite.canvas ? sprite.canvas.height * scale / canvas.height * 100 : 0;

  /** Show only the current window and seek the Remotion player to the IMS frame. */
  function seek(time: number) {
    latestTime.current = time;
    const visible = !!range && configured.current && totalFrames > 0 && time >= range.start && time < range.end;
    setActive(visible);
    const targetFrame = Math.min(totalFrames - 1, Math.max(0, Math.round(time * fps) - startFrame));
    if (visible && targetFrame !== lastFrame.current && frame.current?.contentWindow) {
      lastFrame.current = targetFrame;
      frame.current.contentWindow.postMessage({
        type: "imv-sprite-seek", channel,
        frame: targetFrame,
      }, "*");
    }
  }
  const latestSeek = useRef(seek);
  latestSeek.current = seek;
  useImperativeHandle(ref, () => ({ seek }), [range?.start, range?.end, totalFrames, fps, startFrame, channel]);
  const configure = useRef<() => void>(() => {});
  configure.current = () => {
    if (!ready.current || !frame.current?.contentWindow || !range || totalFrames > 216000) return;
    configured.current = false;
    lastFrame.current = -1;
    setActive(false);
    const next = ++requestId.current;
    frame.current.contentWindow.postMessage({
      type: "imv-sprite-config", channel, requestId: next,
      text, keywords, overrides, totalFrames,
    }, "*");
  };
  useEffect(() => { configure.current(); }, [configKey]);
  useEffect(() => {
    /** Repeat the handshake until fonts and the isolated Player have mounted. */
    function hello() {
      if (!ready.current)
        frame.current?.contentWindow?.postMessage({ type: "imv-sprite-hello", channel }, "*");
    }
    /** Accept acknowledgements only from the matching published Sprite frame and channel. */
    function receive(event: MessageEvent) {
      const data = event.data;
      if (event.source !== frame.current?.contentWindow || data?.channel !== channel) return;
      if (data.type === "imv-sprite-ready") {
        ready.current = true;
        configure.current();
      } else if (data.type === "imv-sprite-rendered" && data.requestId === requestId.current) {
        configured.current = true;
        setStatus("");
        latestSeek.current(latestTime.current);
      } else if (data.type === "imv-sprite-error") {
        configured.current = false;
        setActive(false);
        setStatus(typeof data.message === "string" ? data.message : "Sprite 预览失败");
      }
    }
    window.addEventListener("message", receive);
    const handshake = window.setInterval(hello, 500);
    const timeout = window.setTimeout(() => {
      if (!ready.current) setStatus("Sprite 预览加载超时");
    }, 30_000);
    return () => {
      window.clearInterval(handshake);
      window.clearTimeout(timeout);
      window.removeEventListener("message", receive);
      ready.current = false;
      configured.current = false;
    };
  }, [channel]);
  if (!range || !sprite.canvas || !fps || !scale || totalFrames < 1 || totalFrames > 216000) return null;
  return <>
    <iframe
      ref={frame}
      title={`${sprite.name} 实时叠加预览`}
      src={`${apiBase()}/api/sprites/${encodeURIComponent(sprite.spriteId)}/interactive#${channel}`}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className="pointer-events-none absolute border-0"
      style={{
        width: `${width}%`, height: `${height}%`,
        left: `${(100 - width) / 2}%`, top: `${(100 - height) / 2}%`,
        zIndex: order + 1, visibility: active ? "visible" : "hidden",
      }}
      onLoad={() => frame.current?.contentWindow?.postMessage({ type: "imv-sprite-hello", channel }, "*")}
      onError={() => setStatus("Sprite 预览页面加载失败")}
    />
    {status && <p role="status" className="pointer-events-none absolute bottom-2 left-2 z-[110] rounded bg-background/85 px-2 py-1 text-xs text-foreground">{sprite.name}：{status}</p>}
  </>;
}

/** Expose one seek operation so the SDK remains the only preview clock. */
export function SpritePreviewLayer({
  ref,
  placements,
  catalog,
  duration,
  canvas,
  copy,
  time,
}: {
  ref: Ref<SpritePreviewLayerHandle>;
  placements: SpritePlacement[];
  catalog: SpriteSummary[];
  duration: number;
  canvas: { width: number; height: number } | null;
  copy: Record<string, SpritePreviewCopy>;
  time: number;
}) {
  const children = useRef(new Map<string, ItemHandle>());
  useImperativeHandle(ref, () => ({
    seek(next: number) {
      for (const child of children.current.values()) child.seek(next);
    },
  }), []);
  useEffect(() => {
    for (const child of children.current.values()) child.seek(time);
  }, [placements, catalog, duration, copy, canvas]);
  if (!canvas) return null;
  return <>
    {placements.map((placement, order) => {
      const sprite = catalog.find((item) => item.spriteId === placement.spriteId);
      return sprite && <SpritePreviewItem
        key={placement.id}
        ref={(value) => {
          if (value) children.current.set(placement.id, value);
          else children.current.delete(placement.id);
        }}
        placement={placement}
        sprite={sprite}
        duration={duration}
        canvas={canvas}
        copy={copy[placement.id]}
        order={order}
      />;
    })}
  </>;
}
