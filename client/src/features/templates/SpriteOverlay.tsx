/** 把 Remotion 资产叠在模板预览画面上：每个资产一个透明隔离播放器，由预览播放时间逐帧驱动（只定位，不自行播放）。 */
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { apiBase } from "@/lib/api-base";
import type { SpriteClip } from "./timeline";

/** 父预览仅推送当前时间；播放、暂停、拖动都以同一时间驱动，不会产生时钟漂移。 */
export interface SpriteOverlayHandle {
  setTime(time: number): void;
}

/** 资产片段与模板画面比例；播放器页面加载后才可见，并且只在片段区间内显示。 */
export function SpriteOverlay({ ref, clips, stageAspect, initialTime }: { ref: Ref<SpriteOverlayHandle>; clips: SpriteClip[]; stageAspect: number; initialTime: number }) {
  const frames = useRef(new Map<string, HTMLIFrameElement>());
  const supported = useRef(new Set<string>());
  const time = useRef(initialTime);
  const pending = useRef<number | undefined>(undefined);
  const latest = useRef(clips);
  const [unsupported, setUnsupported] = useState<string[]>([]);
  // 每个播放器使用独立消息通道；通道随片段 ID 稳定，片段移动不重载页面。
  const channels = useRef(new Map<string, string>());
  for (const clip of clips) {
    if (!channels.current.has(clip.id)) channels.current.set(clip.id, crypto.randomUUID());
  }
  latest.current = clips;

  /** 按最新时间切换可见性并发送定位；同一浏览器帧内多次更新只提交最后一次。 */
  function flush() {
    pending.current = undefined;
    for (const clip of latest.current) {
      const frame = frames.current.get(clip.id);
      if (!frame) continue;
      const active = supported.current.has(clip.id) && time.current >= clip.start && time.current < clip.end;
      frame.style.visibility = active ? "visible" : "hidden";
      if (active) frame.contentWindow?.postMessage({ type: "imv-preview-sync", channel: channels.current.get(clip.id), time: time.current - clip.start }, "*");
    }
  }
  /** 合并同一浏览器帧内的更新，回调只读取当前 refs，避免捕获旧通道。 */
  function schedule() {
    if (pending.current === undefined) pending.current = requestAnimationFrame(flush);
  }
  useImperativeHandle(ref, () => ({ setTime(value) { time.current = value; schedule(); } }), []);
  useEffect(() => {
    const ids = new Set(clips.map((clip) => clip.id));
    for (const id of channels.current.keys()) {
      if (!ids.has(id)) { channels.current.delete(id); supported.current.delete(id); }
    }
    setUnsupported((items) => items.some((id) => !ids.has(id)) ? items.filter((id) => ids.has(id)) : items);
    schedule();
  }, [clips]);

  // 页面就绪后才开始定位；旧版本生成的资产不支持逐帧驱动，保持隐藏并提示。
  useEffect(() => {
    function receive(event: MessageEvent) {
      const id = [...frames.current].find(([, frame]) => frame.contentWindow === event.source)?.[0];
      if (!id || event.data?.type !== "imv-preview-ready" || event.data?.channel !== channels.current.get(id)) return;
      if (event.data.sync === true) { supported.current.add(id); schedule(); }
      else setUnsupported((items) => items.includes(id) ? items : [...items, id]);
    }
    window.addEventListener("message", receive);
    return () => {
      window.removeEventListener("message", receive);
      if (pending.current !== undefined) cancelAnimationFrame(pending.current);
      pending.current = undefined;
    };
  }, []);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden={clips.length === 0}>
      {clips.map((clip) => (
        <iframe
          key={clip.id}
          ref={(element) => { if (element) frames.current.set(clip.id, element); else frames.current.delete(clip.id); }}
          title={`Remotion 资产预览：${clip.name}`}
          sandbox="allow-scripts"
          src={`${apiBase()}/api/sprites/${encodeURIComponent(clip.spriteId)}/preview?overlay=true&sync=1#${channels.current.get(clip.id)}`}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 border-0"
          style={{ colorScheme: "light", visibility: "hidden", aspectRatio: clip.aspect, ...(clip.aspect > stageAspect ? { height: "100%" } : { width: "100%" }) }}
        />
      ))}
      {unsupported.length > 0 && <p role="status" className="absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">部分 Remotion 资产由旧版本生成，无法预览；请重新保存到资产。</p>}
    </div>
  );
}
