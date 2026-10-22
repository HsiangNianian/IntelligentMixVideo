/**
 * 把 AI 资产叠在模板预览画面上：每个资产一个透明隔离播放器，由预览播放时间逐帧驱动（只定位，不自行播放）。
 * 只为当前时间附近的片段挂载播放器，离开后卸载；播放器的字体由本页读取一次后转交，不各自下载。
 * 支持参数覆盖的播放器同时收到这次摆放的参数与宿主变换；选中的可移动资产在画面上显示一个可拖动的位置框。
 * 播放器是跨源沙箱页面，无法监听其内部错误：挂载后迟迟不就绪、能力不足或预览页回报错误时写调试日志；
 * 调试模式下另记录偏慢的就绪与参数更新。预览协议没有逐帧回执，定位是否跟得上无法测量。
 */
import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type PointerEvent, type Ref } from "react";
import { apiBase } from "@/lib/api-base";
import { HOST_FONTS, sendPreviewFonts } from "@/features/sprites/previewFonts";
import type { SpriteClip } from "./timeline";
import { debugLog, diagnosticsEnabled } from "@/lib/diagnostics";

/** 父预览仅推送当前时间；播放、暂停、拖动都以同一时间驱动，不会产生时钟漂移。 */
export interface SpriteOverlayHandle {
  setTime(time: number): void;
}

/** 键盘移动位置的步长（画布像素）；按住 Shift 时放大。 */
const KEY_STEP = 10;
/** 片段开始前提前挂载播放器的秒数：页面加载、字体和就绪握手需要时间，播放到片段时已可显示。 */
const MOUNT_AHEAD_SECONDS = 3;
/** 片段结束后保留播放器的秒数，在结束处来回拖动时不反复重新加载。 */
const KEEP_AFTER_SECONDS = 1;
/** 已挂载的播放器离开上述范围后再多保留的秒数：来回拖动游标跨过范围边缘时，不反复创建、销毁页面（每次都要重新加载）。 */
const RELEASE_MARGIN_SECONDS = 2;
/** 播放器挂载后等待就绪通知的时长，与资产封面相同；超时写调试日志（不改变界面，页面仍可能稍后就绪）。 */
const READY_TIMEOUT_MS = 15_000;
/** 调试模式下记录偏慢的握手与更新：就绪超过 3 秒、参数或位置更新到画面超过 0.5 秒。 */
const READY_SLOW_MS = 3000;
const UPDATE_SLOW_MS = 500;

/**
 * 当前时间附近需要挂载播放器的片段；其余片段不创建播放器，每个播放器都是一个独立页面，常驻会占用内存与 CPU。
 * 已挂载的片段按放宽后的范围保留，进入范围才新挂载。
 */
function nearby(clips: SpriteClip[], time: number, mounted: readonly string[]): string[] {
  return clips.filter((clip) => {
    const margin = mounted.includes(clip.id) ? RELEASE_MARGIN_SECONDS : 0;
    return time >= clip.start - MOUNT_AHEAD_SECONDS - margin && time < clip.end + KEEP_AFTER_SECONDS + margin;
  }).map((clip) => clip.id);
}

/** 资产片段与模板画面比例；播放器页面加载后才可见，并且只在片段区间内显示。 */
export function SpriteOverlay({ ref, clips, stageAspect, initialTime, selectedId, onMove, onSelect }: {
  ref: Ref<SpriteOverlayHandle>;
  clips: SpriteClip[];
  stageAspect: number;
  initialTime: number;
  selectedId?: string | null;
  onMove?: (id: string, x: number, y: number) => void;
  onSelect?: (id: string) => void;
}) {
  const frames = useRef(new Map<string, HTMLIFrameElement>());
  const supported = useRef(new Set<string>());
  // 回报 host 能力的播放器接受参数覆盖与宿主变换；sent 记录每个播放器最后一次收到的内容，相同内容不重发。
  const hosts = useRef(new Set<string>());
  const sent = useRef(new Map<string, string>());
  // 每个已挂载播放器的就绪等待计时器，就绪后置为 null；loaded 记录页面 load 事件已触发的播放器，用来区分超时原因。
  const readiness = useRef(new Map<string, ReturnType<typeof setTimeout> | null>());
  const loaded = useRef(new Set<string>());
  // 上一次 load 事件的 iframe 地址；地址变化说明同一播放器重新加载，需要重新握手并等待就绪。
  const loadedSrc = useRef(new Map<string, string>());
  const requests = useRef(0);
  // 调试模式下的计时：播放器挂载的时刻，以及已发出、等待预览页回报画完（imv-preview-rendered）的更新。
  const mountedAt = useRef(new Map<string, number>());
  const updates = useRef(new Map<number, { id: string; at: number }>());
  const time = useRef(initialTime);
  const pending = useRef<number | undefined>(undefined);
  const latest = useRef(clips);
  const [unsupported, setUnsupported] = useState<string[]>([]);
  const [fixed, setFixed] = useState<string[]>([]);
  // 已挂载播放器的片段，随预览时间在 flush 中更新；shown 同步记录最新的集合，让逐帧调用只在集合变化时提交渲染。
  const [mounted, setMounted] = useState(() => nearby(clips, initialTime, []));
  const shown = useRef(mounted);
  // 每个播放器使用独立消息通道；通道随片段 ID 稳定，片段移动不重载页面。
  const channels = useRef(new Map<string, string>());
  for (const clip of clips) {
    if (!channels.current.has(clip.id)) channels.current.set(clip.id, crypto.randomUUID());
  }
  latest.current = clips;

  /** 把片段的参数覆盖与宿主变换发给支持它们的播放器；内容未变时不重发。 */
  function update(clip: SpriteClip) {
    const frame = frames.current.get(clip.id);
    if (!frame || !hosts.current.has(clip.id)) return;
    const values = clip.values ?? {};
    const transform = clip.transform ?? {};
    const key = JSON.stringify([values, transform]);
    if (sent.current.get(clip.id) === key) return;
    sent.current.set(clip.id, key);
    requests.current += 1;
    if (diagnosticsEnabled()) {
      // 预览页没回报时不会删除，积累过多就整体清空。
      if (updates.current.size > 50) updates.current.clear();
      updates.current.set(requests.current, { id: clip.id, at: performance.now() });
    }
    frame.contentWindow?.postMessage({ type: "imv-preview-update", channel: channels.current.get(clip.id), requestId: requests.current, values, background: "", host: { transform } }, "*");
  }
  /** 按最新片段与时间更新需要挂载的播放器；集合不变时不触发渲染。 */
  function remount() {
    const next = nearby(latest.current, time.current, shown.current);
    if (next.join() === shown.current.join()) return;
    shown.current = next;
    setMounted(next);
  }
  /** 按最新时间增减播放器、切换可见性并发送定位；同一浏览器帧内多次更新只提交最后一次。 */
  function flush() {
    pending.current = undefined;
    remount();
    for (const clip of latest.current) {
      const frame = frames.current.get(clip.id);
      if (!frame) continue;
      const active = supported.current.has(clip.id) && time.current >= clip.start && time.current < clip.end;
      frame.style.visibility = active ? "visible" : "hidden";
      // 带上摆放的完整时长：Sprite 按它排布，与总线渲染时一致，而不是停在资产的预览长度。
      if (active) frame.contentWindow?.postMessage({ type: "imv-preview-sync", channel: channels.current.get(clip.id), time: time.current - clip.start, duration: clip.length }, "*");
    }
  }
  /** 合并同一浏览器帧内的更新，回调只读取当前 refs，避免捕获旧通道。 */
  function schedule() {
    if (pending.current === undefined) pending.current = requestAnimationFrame(flush);
  }
  /** 为已挂载的播放器安排就绪超时；跨源沙箱页面无法监听内部报错，超时只能记录资产与地址。 */
  function startReadyTimer(id: string) {
    mountedAt.current.set(id, performance.now());
    readiness.current.set(id, setTimeout(() => {
      readiness.current.set(id, null);
      const clip = latest.current.find((item) => item.id === id);
      const page = loaded.current.has(id) ? "页面已加载但没有发出就绪通知，可能是预览脚本出错" : "页面仍未加载完成，可能是网络或服务端出错";
      debugLog("AI 资产预览", `「${clip?.name ?? id}」（资产 ${clip?.spriteId ?? "未知"}）的播放器 ${READY_TIMEOUT_MS / 1000} 秒内没有就绪：${page}；地址 ${frames.current.get(id)?.src ?? "（已卸载）"}`, "警告");
    }, READY_TIMEOUT_MS));
  }
  /** 同一播放器换资产后页面重新加载：旧握手作废，清掉旧能力标记并重新等待就绪通知。 */
  function reloaded(id: string) {
    clearTimeout(readiness.current.get(id) ?? undefined);
    supported.current.delete(id);
    hosts.current.delete(id);
    sent.current.delete(id);
    setUnsupported((items) => items.includes(id) ? items.filter((item) => item !== id) : items);
    setFixed((items) => items.includes(id) ? items.filter((item) => item !== id) : items);
    startReadyTimer(id);
  }
  useImperativeHandle(ref, () => ({ setTime(value) { time.current = value; schedule(); } }), []);
  useEffect(() => {
    const ids = new Set(clips.map((clip) => clip.id));
    for (const id of channels.current.keys()) {
      if (!ids.has(id)) { channels.current.delete(id); supported.current.delete(id); hosts.current.delete(id); sent.current.delete(id); }
    }
    setUnsupported((items) => items.some((id) => !ids.has(id)) ? items.filter((id) => ids.has(id)) : items);
    setFixed((items) => items.some((id) => !ids.has(id)) ? items.filter((id) => ids.has(id)) : items);
    remount();
    for (const clip of clips) update(clip);
    schedule();
  }, [clips]);

  // 卸载的播放器重新挂载时是新页面，需要重新握手并重发参数。
  useEffect(() => {
    const ids = new Set(mounted);
    for (const id of [...supported.current]) if (!ids.has(id)) supported.current.delete(id);
    for (const id of [...hosts.current]) if (!ids.has(id)) hosts.current.delete(id);
    for (const id of [...sent.current.keys()]) if (!ids.has(id)) sent.current.delete(id);
    for (const id of [...loaded.current]) if (!ids.has(id)) loaded.current.delete(id);
    for (const id of [...loadedSrc.current.keys()]) if (!ids.has(id)) loadedSrc.current.delete(id);
    for (const [id, timer] of readiness.current) if (!ids.has(id)) { clearTimeout(timer ?? undefined); readiness.current.delete(id); }
    for (const id of [...mountedAt.current.keys()]) if (!ids.has(id)) mountedAt.current.delete(id);
    // 跨源沙箱页面的报错无法监听：预览包缺失、服务端出错或脚本崩溃时只会一直不就绪，超时后记录资产与地址。
    for (const id of mounted) {
      if (readiness.current.has(id)) continue;
      startReadyTimer(id);
    }
  }, [mounted]);

  // 播放器请求字体时转交本页读取的字体。页面就绪后才开始定位；旧版本生成的资产不支持逐帧驱动，保持隐藏并提示；
  // 不支持参数覆盖的资产按发布默认值显示并提示。
  useEffect(() => {
    function receive(event: MessageEvent) {
      const entry = [...frames.current].find(([, frame]) => frame.contentWindow === event.source);
      const id = entry?.[0];
      const channel = id ? channels.current.get(id) : undefined;
      if (!entry?.[1].contentWindow || !channel || event.data?.channel !== channel) return;
      if (event.data?.type === "imv-preview-fonts-request") return sendPreviewFonts(entry[1].contentWindow, channel);
      if (!id) return;
      const clip = latest.current.find((item) => item.id === id);
      const label = `「${clip?.name ?? id}」（资产 ${clip?.spriteId ?? "未知"}）`;
      // 预览页自己发现的问题（如背景或字体加载失败）原来被忽略，现在写进日志。
      if (event.data?.type === "imv-preview-error") {
        debugLog("AI 资产预览", `${label}的预览页报错：${String(event.data.message ?? "未知错误")}`);
        return;
      }
      if (event.data?.type === "imv-preview-rendered") {
        const request = updates.current.get(event.data.requestId);
        if (!request) return;
        updates.current.delete(event.data.requestId);
        const elapsed = performance.now() - request.at;
        if (elapsed > UPDATE_SLOW_MS) debugLog("AI 资产预览", `${label}参数或位置更新到画面用时 ${Math.round(elapsed)} ms`, "警告");
        return;
      }
      if (event.data?.type !== "imv-preview-ready") return;
      clearTimeout(readiness.current.get(id) ?? undefined);
      if (readiness.current.has(id)) readiness.current.set(id, null);
      const since = mountedAt.current.get(id);
      if (diagnosticsEnabled() && since !== undefined && performance.now() - since > READY_SLOW_MS)
        debugLog("AI 资产预览", `${label}的播放器就绪用时 ${((performance.now() - since) / 1000).toFixed(1)} 秒`, "信息");
      if (event.data.sync !== true) {
        debugLog("AI 资产预览", `${label}的预览包较旧，不支持逐帧驱动，保持隐藏`, "警告");
        setUnsupported((items) => items.includes(id) ? items : [...items, id]);
        return;
      }
      supported.current.add(id);
      if (event.data.host === true) {
        hosts.current.add(id);
        sent.current.delete(id);
        const clip = latest.current.find((item) => item.id === id);
        if (clip) update(clip);
      } else {
        debugLog("AI 资产预览", `${label}的预览包不支持参数覆盖，按发布默认值显示`, "信息");
        setFixed((items) => items.includes(id) ? items : [...items, id]);
      }
      schedule();
    }
    window.addEventListener("message", receive);
    const timers = readiness.current;
    return () => {
      window.removeEventListener("message", receive);
      for (const timer of timers.values()) clearTimeout(timer ?? undefined);
      timers.clear();
      if (pending.current !== undefined) cancelAnimationFrame(pending.current);
      pending.current = undefined;
    };
  }, []);

  const selected = clips.find((clip) => clip.id === selectedId && clip.canvas?.movable);
  const active = new Set(mounted);
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden={clips.length === 0}>
      {clips.filter((clip) => active.has(clip.id)).map((clip) => (
        <iframe
          key={clip.id}
          ref={(element) => { if (element) frames.current.set(clip.id, element); else frames.current.delete(clip.id); }}
          title={`AI 资产预览：${clip.name}`}
          sandbox="allow-scripts"
          onLoad={(event) => {
            loaded.current.add(clip.id);
            const src = event.currentTarget.src;
            const previous = loadedSrc.current.get(clip.id);
            loadedSrc.current.set(clip.id, src);
            if (previous !== undefined && previous !== src) reloaded(clip.id);
          }}
          src={`${apiBase()}/api/sprites/${encodeURIComponent(clip.spriteId)}/preview?overlay=true&sync=1&${HOST_FONTS}#${channels.current.get(clip.id)}`}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 border-0"
          style={{ colorScheme: "light", visibility: "hidden", aspectRatio: clip.aspect, ...(clip.aspect > stageAspect ? { height: "100%" } : { width: "100%" }) }}
        />
      ))}
      {selected && onMove && <PositionHandle clip={selected} stageAspect={stageAspect} onMove={onMove} />}
      {onSelect && clips.filter((clip) => clip.textTrackId && clip.canvas?.movable && clip.id !== selectedId && initialTime >= clip.start && initialTime < clip.end).map((clip) =>
        <PositionHandle key={clip.id} clip={clip} stageAspect={stageAspect} onSelect={onSelect} />)}
      {unsupported.length > 0 && <p role="status" className="absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">部分 AI 资产由旧版本生成，无法预览；请重新保存到资产。</p>}
      {fixed.length > 0 && <p role="status" className="absolute bottom-2 left-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">部分 AI 资产的预览不显示参数调整，请重新保存到资产；保存模板后的渲染不受影响。</p>}
    </div>
  );
}

/** 选中资产内容区域的可拖动框：按播放器在画面里的实际宽度把指针位移换算为画布像素，并限制在画布内。 */
function PositionHandle({ clip, stageAspect, onMove, onSelect }: { clip: SpriteClip; stageAspect: number; onMove?: (id: string, x: number, y: number) => void; onSelect?: (id: string) => void }) {
  const area = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; clientX: number; clientY: number; x: number; y: number } | null>(null);
  const { width, height, box } = clip.canvas!;
  const scale = clip.transform?.scale ?? 1;
  const x = clip.transform?.x ?? box.x + box.width / 2;
  const y = clip.transform?.y ?? box.y + box.height / 2;
  /** 位置限制在画布内并取整。 */
  function move(nextX: number, nextY: number) {
    onMove?.(clip.id, Math.round(Math.min(width, Math.max(0, nextX))), Math.round(Math.min(height, Math.max(0, nextY))));
  }
  /** 拖动中按显示比例换算位移。 */
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current;
    const shown = area.current?.getBoundingClientRect().width;
    if (!start || start.pointer !== event.pointerId || !shown) return;
    const ratio = width / shown;
    move(start.x + (event.clientX - start.clientX) * ratio, start.y + (event.clientY - start.clientY) * ratio);
  }
  /** 方向键按步长移动，按住 Shift 时步长放大五倍。 */
  function keyMove(event: KeyboardEvent<HTMLDivElement>) {
    const step = KEY_STEP * (event.shiftKey ? 5 : 1);
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!delta) return;
    event.preventDefault();
    move(x + delta[0]!, y + delta[1]!);
  }
  const aspect = width / height;
  return (
    <div ref={area} className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ aspectRatio: aspect, ...(aspect > stageAspect ? { height: "100%" } : { width: "100%" }) }}>
      <div
        role={onMove ? "slider" : "button"}
        tabIndex={0}
        aria-label={`${onMove ? "拖动调整位置" : "选择文字"}：${clip.name}`}
        aria-valuemin={0}
        aria-valuemax={width}
        aria-valuenow={Math.round(x)}
        aria-valuetext={`横向 ${Math.round(x)}，纵向 ${Math.round(y)}`}
        className={`pointer-events-auto absolute rounded-sm border-2 border-dashed outline-none focus-visible:ring-2 focus-visible:ring-ring ${onMove ? "cursor-move border-primary bg-primary/5" : "border-transparent hover:border-primary"}`}
        style={{
          left: `${((x - (box.width * scale) / 2) / width) * 100}%`,
          top: `${((y - (box.height * scale) / 2) / height) * 100}%`,
          width: `${((box.width * scale) / width) * 100}%`,
          height: `${((box.height * scale) / height) * 100}%`,
          rotate: `${clip.transform?.rotation ?? 0}deg`,
        }}
        onPointerDown={(event) => {
          onSelect?.(clip.id);
          if (!onMove) return;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          drag.current = { pointer: event.pointerId, clientX: event.clientX, clientY: event.clientY, x, y };
        }}
        onPointerMove={pointerMove}
        onPointerUp={() => { drag.current = null; }}
        onPointerCancel={() => { drag.current = null; }}
        onKeyDown={(event) => { if (onSelect && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(clip.id); } else keyMove(event); }}
      />
    </div>
  );
}
