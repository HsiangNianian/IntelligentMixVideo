/**
 * 把 Remotion 资产叠在模板预览画面上：每个资产一个透明隔离播放器，由预览播放时间逐帧驱动（只定位，不自行播放）。
 * 支持参数覆盖的播放器同时收到这次摆放的参数与宿主变换；选中的可移动资产在画面上显示一个可拖动的位置框。
 * 句中原位的关键词：所属文字的播放器隐藏自己标记的关键词并回报它的位置，关键词播放器回报自己文字的静止位置，
 * 这里把关键词播放器的宿主变换算到那个位置上（两个页面互相隔离，只能经父页面传递）。
 */
import { useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent, type PointerEvent, type Ref } from "react";
import { apiBase } from "@/lib/api-base";
import type { ClipTransform, ContentBox } from "@/features/sprites/parameters";
import type { SpriteClip } from "./timeline";

/** 预览页回报的画布像素矩形。 */
interface Box { x: number; y: number; width: number; height: number }
/** 所属文字标记的关键词：文字（去掉空白）、字形框与绘制时的不透明度。 */
interface Spot extends Box { text: string; opacity: number }
/** 预览页（`measure=true`）逐帧回报的度量：标记的关键词，以及内容自身文字不含动画变换的框。 */
interface Measure { spots: Spot[]; text: Box | null }

/** 校验来自隔离页面的矩形：四个有限数字。 */
function box(value: unknown): value is Box {
  return !!value && typeof value === "object" && ["x", "y", "width", "height"].every((key) => Number.isFinite((value as Record<string, unknown>)[key]));
}

/** 解析 `imv-preview-measure` 消息；形状不对时返回 null。 */
function measureOf(data: { spots?: unknown; text?: unknown }): Measure | null {
  if (!Array.isArray(data.spots) || data.spots.length > 100 || !(data.text === null || box(data.text))) return null;
  const spots = data.spots.filter((spot): spot is Spot => box(spot) && typeof (spot as Partial<Spot>).text === "string" && Number.isFinite((spot as Partial<Spot>).opacity));
  return spots.length === data.spots.length ? { spots, text: data.text as Box | null } : null;
}

/**
 * 与 `remotion/keyword-anchor.js` 的 `placement` 相同：把关键词字效静止时的文字框 `rest` 放到原句关键词 `spot` 上，
 * 取关键词的字高与不透明度；宿主变换绕内容区域 `box` 的中心移动与缩放。
 */
export function anchorTransform(spot: Spot, rest: Box, box: ContentBox): ClipTransform {
  const scale = spot.height / rest.height;
  return {
    x: spot.x + spot.width / 2 - scale * (rest.x + rest.width / 2 - (box.x + box.width / 2)),
    y: spot.y + spot.height / 2 - scale * (rest.y + rest.height / 2 - (box.y + box.height / 2)),
    scale, opacity: spot.opacity,
  };
}

/** 父预览仅推送当前时间；播放、暂停、拖动都以同一时间驱动，不会产生时钟漂移。 */
export interface SpriteOverlayHandle {
  setTime(time: number): void;
}

/** 键盘移动位置的步长（画布像素）；按住 Shift 时放大。 */
const KEY_STEP = 10;

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
  const requests = useRef(0);
  const time = useRef(initialTime);
  const pending = useRef<number | undefined>(undefined);
  const latest = useRef(clips);
  const [unsupported, setUnsupported] = useState<string[]>([]);
  const [fixed, setFixed] = useState<string[]>([]);
  // 每个播放器使用独立消息通道；通道随片段 ID 稳定，片段移动不重载页面。
  const channels = useRef(new Map<string, string>());
  // 句中原位用到的播放器最近一次回报的度量。
  const measures = useRef(new Map<string, Measure>());
  for (const clip of clips) {
    if (!channels.current.has(clip.id)) channels.current.set(clip.id, crypto.randomUUID());
  }
  latest.current = clips;

  /** 片段的宿主变换：句中原位的关键词按两边的度量放到原句的关键词上，还没有度量时为 null（先不显示）。 */
  function placed(clip: SpriteClip): ClipTransform | null {
    if (!clip.anchor) return clip.transform ?? {};
    const spots = measures.current.get(clip.anchor)?.spots ?? [];
    const word = String(clip.values?.text ?? "").replace(/\s+/g, "");
    const spot = spots.find((item) => item.text === word) ?? spots[0];
    const rest = measures.current.get(clip.id)?.text;
    return spot && rest?.height && clip.canvas ? anchorTransform(spot, rest, clip.canvas.box) : null;
  }
  /** 把片段的参数覆盖与宿主变换发给支持它们的播放器；内容未变时不重发。 */
  function update(clip: SpriteClip) {
    const frame = frames.current.get(clip.id);
    if (!frame || !hosts.current.has(clip.id)) return;
    const values = clip.values ?? {};
    const transform = placed(clip);
    if (!transform) return;
    const key = JSON.stringify([values, transform]);
    if (sent.current.get(clip.id) === key) return;
    sent.current.set(clip.id, key);
    requests.current += 1;
    frame.contentWindow?.postMessage({ type: "imv-preview-update", channel: channels.current.get(clip.id), requestId: requests.current, values, background: "", host: { transform } }, "*");
  }
  /** 按最新时间切换可见性并发送定位；同一浏览器帧内多次更新只提交最后一次。 */
  function flush() {
    pending.current = undefined;
    for (const clip of latest.current) {
      const frame = frames.current.get(clip.id);
      if (!frame) continue;
      const active = supported.current.has(clip.id) && time.current >= clip.start && time.current < clip.end && (!clip.anchor || placed(clip) !== null);
      frame.style.visibility = active ? "visible" : "hidden";
      // 带上摆放的完整时长：Sprite 按它排布，与总线渲染时一致，而不是停在资产的预览长度。
      if (active) frame.contentWindow?.postMessage({ type: "imv-preview-sync", channel: channels.current.get(clip.id), time: time.current - clip.start, duration: clip.length }, "*");
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
      if (!ids.has(id)) { channels.current.delete(id); supported.current.delete(id); hosts.current.delete(id); sent.current.delete(id); measures.current.delete(id); }
    }
    setUnsupported((items) => items.some((id) => !ids.has(id)) ? items.filter((id) => ids.has(id)) : items);
    setFixed((items) => items.some((id) => !ids.has(id)) ? items.filter((id) => ids.has(id)) : items);
    for (const clip of clips) update(clip);
    schedule();
  }, [clips]);

  // 页面就绪后才开始定位；旧版本生成的资产不支持逐帧驱动，保持隐藏并提示；不支持参数覆盖的资产按发布默认值显示并提示。
  useEffect(() => {
    function receive(event: MessageEvent) {
      const id = [...frames.current].find(([, frame]) => frame.contentWindow === event.source)?.[0];
      if (!id || event.data?.channel !== channels.current.get(id)) return;
      if (event.data?.type === "imv-preview-measure") {
        const measure = measureOf(event.data);
        if (!measure) return;
        measures.current.set(id, measure);
        // 所属文字或关键词自己的度量变了，重新放置依赖它的关键词。
        for (const clip of latest.current) if (clip.anchor && (clip.anchor === id || clip.id === id)) update(clip);
        schedule();
        return;
      }
      if (event.data?.type !== "imv-preview-ready") return;
      if (event.data.sync !== true) { setUnsupported((items) => items.includes(id) ? items : [...items, id]); return; }
      supported.current.add(id);
      if (event.data.host === true) {
        hosts.current.add(id);
        sent.current.delete(id);
        const clip = latest.current.find((item) => item.id === id);
        if (clip) update(clip);
      } else setFixed((items) => items.includes(id) ? items : [...items, id]);
      schedule();
    }
    window.addEventListener("message", receive);
    return () => {
      window.removeEventListener("message", receive);
      if (pending.current !== undefined) cancelAnimationFrame(pending.current);
      pending.current = undefined;
    };
  }, []);

  const selected = clips.find((clip) => clip.id === selectedId && clip.canvas?.movable);
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden={clips.length === 0}>
      {clips.map((clip) => (
        <iframe
          key={clip.id}
          ref={(element) => { if (element) frames.current.set(clip.id, element); else frames.current.delete(clip.id); }}
          title={`Remotion 资产预览：${clip.name}`}
          sandbox="allow-scripts"
          src={`${apiBase()}/api/sprites/${encodeURIComponent(clip.spriteId)}/preview?overlay=true&sync=1${clip.anchor || clip.hideKeyword ? "&measure=true" : ""}${clip.hideKeyword ? "&hide_keyword=true" : ""}#${channels.current.get(clip.id)}`}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 border-0"
          style={{ colorScheme: "light", visibility: "hidden", aspectRatio: clip.aspect, ...(clip.aspect > stageAspect ? { height: "100%" } : { width: "100%" }) }}
        />
      ))}
      {selected && onMove && <PositionHandle clip={selected} stageAspect={stageAspect} onMove={onMove} />}
      {onSelect && clips.filter((clip) => clip.textTrackId && clip.canvas?.movable && clip.id !== selectedId && initialTime >= clip.start && initialTime < clip.end).map((clip) =>
        <PositionHandle key={clip.id} clip={clip} stageAspect={stageAspect} onSelect={onSelect} />)}
      {unsupported.length > 0 && <p role="status" className="absolute left-2 top-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">部分 Remotion 资产由旧版本生成，无法预览；请重新保存到资产。</p>}
      {fixed.length > 0 && <p role="status" className="absolute bottom-2 left-2 rounded bg-black/60 px-2 py-1 text-[11px] text-white">部分 Remotion 资产的预览不显示参数调整，请重新保存到资产；保存模板后的渲染不受影响。</p>}
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
