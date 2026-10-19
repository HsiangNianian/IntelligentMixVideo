/** AI 资产封面：在卡片里加载资产自己的隔离同步预览页，并定格到动画中的几帧；封面只读取现有预览接口，不新增服务端产物。 */
import { useEffect, useRef, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { apiBase } from "@/lib/api-base";
import type { SpriteAsset } from "@/features/sprites/model";
import { CoverPlaceholder } from "./AssetBrowser";

/** 截取的位置占资产时长的比例：第一项是静止封面，其余在悬停或聚焦时依次轮播。 */
const COVER_POSITIONS = [0.5, 0.2, 0.8];
/** 悬停轮播的帧间隔。 */
const CYCLE_MS = 700;
/** 播放器收到定位后绘制新帧所需的等待，之后才显示，避免闪现第 0 帧。 */
const SHOW_DELAY_MS = 150;
/** 预览页迟迟不报告就绪时放弃，改为显示加载失败。 */
const READY_TIMEOUT_MS = 15_000;

type Status = "loading" | "ready" | "unsupported" | "failed";

/** 用户要求减少动态效果时只显示静止封面，不轮播。 */
function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/**
 * 封面按资产画布比例显示，只在进入视口时挂载预览页，离开视口即卸载，卡片多时同时存在的播放器数量受滚动区域限制。
 * 就绪握手沿用叠加预览的 `imv-preview-ready` / `imv-preview-sync` 协议：旧版本资产不支持逐帧定位，显示“暂无封面”。
 */
export function SpriteCover({ asset, active }: { asset: SpriteAsset; active: boolean }) {
  const [channel] = useState(() => crypto.randomUUID());
  const box = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const [visible, setVisible] = useState(false);
  const [status, setStatus] = useState<Status>("loading");
  const [position, setPosition] = useState(0);
  const [shown, setShown] = useState(false);
  const aspect = Math.min(2, Math.max(0.5, asset.aspect));

  // 可见性：进入视口才加载；离开视口卸载并回到初始状态，已判定为不支持或失败的资产不重复尝试。
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") return setVisible(true);
    const observer = new IntersectionObserver(([entry]) => {
      setVisible(entry.isIntersecting);
      if (entry.isIntersecting) return;
      setStatus((value) => value === "ready" ? "loading" : value);
      setShown(false);
      setPosition(0);
    }, { rootMargin: "80px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // 握手：只接受本卡片播放器、本通道的就绪与错误通知。
  useEffect(() => {
    if (!visible) return;
    function receive(event: MessageEvent) {
      if (event.source !== frame.current?.contentWindow || event.data?.channel !== channel) return;
      if (event.data.type === "imv-preview-ready") setStatus(event.data.sync === true ? "ready" : "unsupported");
      else if (event.data.type === "imv-preview-error") setStatus("failed");
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [visible, channel]);

  useEffect(() => {
    if (!visible || status !== "loading") return;
    const timer = setTimeout(() => setStatus("failed"), READY_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [visible, status]);

  // 定位：就绪或切换帧位置时发送时间。
  useEffect(() => {
    if (status !== "ready") return;
    frame.current?.contentWindow?.postMessage({ type: "imv-preview-sync", channel, time: Number((asset.seconds * COVER_POSITIONS[position]).toFixed(3)) }, "*");
  }, [status, position, asset.seconds, channel]);

  // 首次定位后稍等再显示，播放器已绘制目标帧。
  useEffect(() => {
    if (status !== "ready") return;
    const timer = setTimeout(() => setShown(true), SHOW_DELAY_MS);
    return () => clearTimeout(timer);
  }, [status]);

  // 轮播：卡片被悬停或聚焦时立即换到下一帧并按间隔继续，离开后回到静止封面。
  useEffect(() => {
    if (!active) return setPosition(0);
    if (status !== "ready" || reducedMotion()) return;
    const next = () => setPosition((value) => (value + 1) % COVER_POSITIONS.length);
    next();
    const timer = setInterval(next, CYCLE_MS);
    return () => clearInterval(timer);
  }, [active, status]);

  const unavailable = status === "unsupported" || status === "failed";
  return (
    <div ref={box} aria-hidden="true" className="relative w-full overflow-hidden" style={{ aspectRatio: aspect }}>
      {visible && !unavailable && (
        <iframe
          ref={frame}
          title={`AI 资产封面：${asset.name}`}
          sandbox="allow-scripts"
          tabIndex={-1}
          src={`${apiBase()}/api/sprites/${encodeURIComponent(asset.id)}/preview?sync=1#${channel}`}
          className="pointer-events-none absolute inset-0 h-full w-full border-0"
          style={{ colorScheme: "light", visibility: shown ? "visible" : "hidden" }}
        />
      )}
      {!unavailable && !shown && <Skeleton className="absolute inset-0 rounded-none" />}
      {unavailable && <div className="absolute inset-0 flex items-center justify-center"><CoverPlaceholder text={status === "failed" ? "封面加载失败" : "暂无封面"} /></div>}
    </div>
  );
}
