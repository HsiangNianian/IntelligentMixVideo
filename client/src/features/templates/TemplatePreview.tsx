/** SDK 预览组件：管理单个播放器、串行更新时间线，卸载时清理订阅和异步任务。 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Maximize2, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { Spinner } from "@/components/ui/spinner";
import type { Draft, EffectAsset, MasterVideo } from "./model";
import { loadSDK, loadPreviewFont, readCatalog, type Player } from "./sdk";
import { buildTimeline, buildPreviewRows, spriteRows, type SpriteClip } from "./timeline";
import { PreviewTimeline, type PreviewTimelineHandle } from "./PreviewTimeline";
import { SpriteOverlay, type SpriteOverlayHandle } from "./SpriteOverlay";
import { previewDuration } from "./tracks";
import { previewVideoUrl, readMasterVideo } from "./media";
import { MasterVideoInput } from "./MasterVideoInput";

/** 编辑状态由父组件持有；目录只在 SDK 初始化成功后回传。 */
interface Props {
  draft: Draft;
  media?: MasterVideo;
  onMediaChange: (media: MasterVideo) => void;
  videoInputKey: string;
  onCatalog: (catalog: EffectAsset[]) => void;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onRangeChange?: (id: string, start: number, end: number) => void;
  sprites?: SpriteClip[];
}

/** 时间轴标记由三条片段和一条播放指针组成。 */
function TimelineMark() {
  return <span className="template-timeline-mark flex size-8 items-center justify-center rounded-lg" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" className="size-5"><rect x="2" y="7" width="8" height="4" rx="1" fill="currentColor" /><rect x="14" y="7" width="8" height="4" rx="1" fill="currentColor" /><rect x="5" y="15" width="13" height="4" rx="1" fill="currentColor" /><path d="M12 4v17m-2-18h4l-2 3-2-3Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg></span>;
}

/** 每次修改全量更新时间线并回到开头；串行处理，快速修改只应用最新草稿。 */
export function TemplatePreview({ draft, media, onMediaChange, videoInputKey, onCatalog, selectedId, onSelect, onRangeChange, sprites = [] }: Props) {
  const duration = Math.max(0, previewDuration(draft, media));
  const stage = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const player = useRef<Player | null>(null);
  const latest = useRef(draft);
  const latestMedia = useRef(media);
  const apply = useRef<(() => void) | null>(null);
  const catalogCallback = useRef(onCatalog);
  const playAction = useRef<((start: number, end?: number) => void) | null>(null);
  const cancelPlaybackAction = useRef<(() => void) | null>(null);
  const seekAction = useRef<((time: number) => void) | null>(null);
  const track = useRef<PreviewTimelineHandle>(null);
  const overlay = useRef<SpriteOverlayHandle>(null);
  const [rows, setRows] = useState<ReturnType<typeof buildPreviewRows>>([]);
  const timelineRows = useMemo(() => [...rows, ...spriteRows(sprites)], [rows, sprites]);
  const [notices, setNotices] = useState<string[]>([]);
  const transition = rows.flatMap((row) => row.actions).find((item) => item.effectId === "transition");
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("正在加载预览组件…");
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [time, setTime] = useState(0);
  const [canvas, setCanvas] = useState<Pick<MasterVideo, "width" | "height"> | null>(null);
  const playing = status === "正在播放预览…" || status === "正在预览转场…";
  latest.current = draft;
  latestMedia.current = media;
  catalogCallback.current = onCatalog;

  useEffect(() => {
    let disposed = false;
    let active = false;
    let revision = 0;
    let instance: Player | null = null;
    let frame: HTMLIFrameElement | undefined;
    let exampleMedia: MasterVideo | undefined;
    const controller = new AbortController();
    let catalog: EffectAsset[] = [];
    let appliedTracks: Draft["tracks"] | undefined;
    let appliedMedia: MasterVideo | undefined;
    let subscription: { unsubscribe(): void } | undefined;
    let seekTarget: number | null = null;
    let resumeAfterSeek = false;
    let seekFrame: number | undefined;
    let seekTimer: ReturnType<typeof setTimeout> | undefined;
    let transitionEnd = 0;
    let displayedDecisecond = 0;
    let playTimer: ReturnType<typeof setTimeout> | undefined;
    setReady(false);
    setFailed(false);
    setStatus("正在加载预览组件…");

    /** 取消尚未完成的定位与播放，暂停、更新和卸载共用此清理入口。 */
    const cancel = () => {
      seekTarget = null;
      resumeAfterSeek = false;
      transitionEnd = 0;
      clearTimeout(playTimer);
      clearTimeout(seekTimer);
      if (seekFrame !== undefined) cancelAnimationFrame(seekFrame);
      seekFrame = undefined;
      if (!disposed) setSeeking(false);
    };

    /** 定位超过等待时间时释放请求并展示错误，用户可重新加载当前预览。 */
    const watchSeek = () => {
      setSeeking(true);
      clearTimeout(seekTimer);
      seekTimer = setTimeout(() => {
        cancel();
        instance?.pause();
        setReady(false);
        setFailed(true);
        setStatus("预览定位超时，请重试预览。");
      }, 10_000);
    };

    /** SDK 确认定位完成后才恢复播放，完成通知与画面更新分别处理。 */
    const finishSeek = () => {
      seekTarget = null;
      setSeeking(false);
      clearTimeout(seekTimer);
      if (resumeAfterSeek) {
        resumeAfterSeek = false;
        playTimer = setTimeout(() => {
          if (!disposed) {
            instance?.play();
            setStatus(transitionEnd ? "正在预览转场…" : "正在播放预览…");
          }
        }, 0);
      } else setStatus("预览已暂停");
    };

    // 避免异步旧时间线覆盖新配置；卸载后不再更新 React 状态。
    const update = async () => {
      if (!instance || active || disposed) return;
      active = true;
      setReady(false);
      setFailed(false);
      let applied = revision;
      try {
        do {
          applied = revision;
          cancel();
          const currentDraft = latest.current;
          const currentMedia = latestMedia.current;
          const timeline = buildTimeline(currentDraft, catalog, currentMedia ?? exampleMedia);
          if (disposed) return;
          instance.pause();
          instance.aspectRatio = timeline.AspectRatio;
          setStatus("正在应用效果并加载媒体…");
          const { previewRows: _rows, notices: adjustments, ...sdkTimeline } = timeline;
          // 清空 SDK 缓存的旧时间线，确保文字更新后只显示当前内容。
          instance.timeline = {};
          await instance.setTimeline(sdkTimeline);
          if (!disposed && applied === revision) {
            appliedTracks = currentDraft.tracks;
            appliedMedia = currentMedia;
            setRows(buildPreviewRows(timeline));
            setNotices(adjustments);
          }
        } while (!disposed && applied !== revision);
        if (!disposed) {
          instance.currentTime = 0;
          displayedDecisecond = 0;
          setTime(0);
          track.current?.setTime(0);
          overlay.current?.setTime(0);
          setReady(true);
          setStatus("预览已就绪，点击播放查看效果");
        }
      } catch (error) {
        if (!disposed) {
          setStatus(error instanceof Error ? error.message : "预览失败");
          setFailed(true);
        }
      } finally {
        active = false;
        if (!disposed && applied !== revision) void update();
      }
    };
    apply.current = () => {
      // 初始化已应用相同输入时，延迟通知无需再次重置播放位置。
      if (appliedTracks === latest.current.tracks && appliedMedia === latestMedia.current) return;
      revision++;
      void update();
    };
    // 示例也读取真实尺寸，但保留十秒预览区间；每种尺寸使用独立 SDK 文档。
    void Promise.all([
      media ? Promise.resolve(media) : readMasterVideo(previewVideoUrl(), controller.signal),
      loadPreviewFont(),
    ])
      .then(async ([source]) => {
        if (disposed || !container.current) return;
        if (!media) exampleMedia = { ...source, duration: 10 };
        setCanvas({ width: source.width, height: source.height });
        frame = document.createElement("iframe");
        frame.title = "模板预览播放器";
        frame.allow = "autoplay";
        frame.style.cssText = "display:block;width:100%;height:100%;border:0";
        // 使用应用自身 URL，让 SDK 能读取实际主机并完成 localhost 授权检查。
        const previewFrame = frame;
        await new Promise<void>((resolve, reject) => {
          const finish = (error?: Error) => {
            clearTimeout(timeout);
            previewFrame.onload = previewFrame.onerror = null;
            controller.signal.removeEventListener("abort", aborted);
            if (error) reject(error); else resolve();
          };
          const aborted = () => finish(new DOMException("预览页面加载已取消", "AbortError"));
          const timeout = setTimeout(() => finish(new Error("预览页面加载超时，请重试")), 10_000);
          previewFrame.onload = () => finish();
          previewFrame.onerror = () => finish(new Error("预览页面加载失败，请重试"));
          controller.signal.addEventListener("abort", aborted, { once: true });
          previewFrame.src = new URL("/template-preview.html", window.location.href).href;
          container.current!.append(previewFrame);
        });
        if (disposed) return;
        const target = frame.contentDocument!;
        target.documentElement.style.height = "100%";
        target.body.style.cssText = "margin:0;height:100%;overflow:hidden";
        const surface = target.createElement("div");
        surface.style.cssText = "width:100%;height:100%";
        target.body.append(surface);
        const sdk = await loadSDK(target, controller.signal);
        if (disposed) return;
        instance = new sdk({
          container: surface,
          mode: "component",
          controls: true,
          locale: "zh-CN",
          licenseConfig: { rootDomain: "", licenseKey: "" },
          aspectRatio: `${source.width}:${source.height}`,
          // SDK 按 16:9 的包围区域计算场景尺寸，选取受限制的一边以保留真实宽高。
          maxCanvasConfig: source.width / source.height >= 16 / 9
            ? { width: source.width } : { height: source.height },
          getMediaInfo: async (id, _type, _origin, url) => url || id,
          getTimelineMaterials: async (materials) =>
            materials.map((item) => ({ ...item, video: { duration: latestMedia.current?.duration ?? exampleMedia?.duration ?? 10 } })),
        });
        // SDK 在初始化 Promise 中应用场景尺寸，等待该过程后设置 Timeline。
        await Promise.resolve();
        if (disposed) return;
        catalog = readCatalog(sdk);
        catalogCallback.current(catalog);
        player.current = instance;
        subscription = instance.event$.subscribe((event) => {
          if (disposed || active || !instance) return;
          if (event.type === "playerSeeked") {
            if (seekTarget !== null && event.data?.currentTime !== undefined && Math.abs(event.data.currentTime - seekTarget) <= 0.05)
              finishSeek();
            return;
          }
          if (event.type !== "render") return;
          const current = instance.currentTime;
          if (seekTarget !== null) {
            if (Math.abs(current - seekTarget) > 0.05) return;
          }
          track.current?.setTime(current);
          overlay.current?.setTime(current);
          // SDK 仍逐帧驱动播放控制，界面时间最多每 0.1 秒渲染一次。
          const nextDecisecond = Math.min(
            previewDuration(latest.current, latestMedia.current) * 10,
            Math.max(0, Math.round(current * 10)),
          );
          if (nextDecisecond !== displayedDecisecond) {
            displayedDecisecond = nextDecisecond;
            setTime(nextDecisecond / 10);
          }
          if (transitionEnd && current >= transitionEnd) {
            instance.pause();
            transitionEnd = 0;
            setStatus("转场预览结束");
          } else if (current >= previewDuration(latest.current, latestMedia.current)) {
            setStatus("预览结束，点击播放可重播");
          }
        });
        void update();
      })
      .catch((error) => {
        if (!disposed) {
          setStatus(error instanceof Error ? error.message : "SDK 初始化失败");
          setFailed(true);
        }
      });
    // 普通重播与转场共用定位后播放；暂停、更新或卸载时取消尚未开始的播放。
    playAction.current = (start, end = 0) => {
      if (!instance || active || disposed) return;
      cancel();
      seekTarget = start;
      resumeAfterSeek = true;
      transitionEnd = end;
      setStatus("正在定位播放位置…");
      if (Math.abs(instance.currentTime - start) < 0.001) finishSeek();
      else {
        instance.pause();
        watchSeek();
        instance.currentTime = start;
      }
    };
    cancelPlaybackAction.current = cancel;
    // 输入立即更新游标，一次浏览器绘制只提交最新定位；保持暂停并等待 SDK 确认。
    seekAction.current = (value) => {
      if (!instance || active || disposed || !Number.isFinite(value)) return;
      const wasSeeking = seekTarget !== null;
      cancel();
      instance.pause();
      const target = Math.min(previewDuration(latest.current, latestMedia.current), Math.max(0, value));
      seekTarget = target;
      displayedDecisecond = Math.round(target * 10);
      setTime(target);
      track.current?.setTime(target);
      overlay.current?.setTime(target);
      setStatus("正在定位播放位置…");
      watchSeek();
      seekFrame = requestAnimationFrame(() => {
        seekFrame = undefined;
        if (!instance || disposed) return;
        if (!wasSeeking && Math.abs(instance.currentTime - target) < 0.001) {
          finishSeek();
        } else instance.currentTime = target;
      });
    };
    return () => {
      disposed = true;
      controller.abort();
      cancel();
      subscription?.unsubscribe();
      playAction.current = null;
      cancelPlaybackAction.current = null;
      seekAction.current = null;
      apply.current = null;
      player.current = null;
      instance?.destroy();
      frame?.remove();
    };
  }, [attempt, media?.width, media?.height]);

  useEffect(() => {
    const timer = window.setTimeout(() => apply.current?.(), 250);
    return () => window.clearTimeout(timer);
  }, [draft.tracks, media]);

  return <section aria-label="实时预览" className="flex min-h-full min-w-0 flex-col">
    <div className="template-preview-stage border-b px-4 pb-3 pt-4 lg:px-7 lg:pt-5"><div className="mx-auto max-w-[900px]">
      <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="template-live-dot size-1.5 rounded-full" aria-hidden="true" /><h2 className="text-[13px] font-semibold">实时预览</h2></div><span className="rounded-md border px-2 py-1 text-[11px] tabular-nums text-muted-foreground">{canvas ? `${canvas.width} × ${canvas.height}` : "正在读取画布尺寸"}</span></div>
      <div ref={stage} className="template-preview-canvas relative mx-auto aspect-video w-full overflow-hidden rounded-lg bg-black" style={canvas ? { aspectRatio: `${canvas.width} / ${canvas.height}`, maxWidth: `${48 * canvas.width / canvas.height}dvh`, "--preview-ratio": canvas.width / canvas.height } as CSSProperties : undefined} aria-label="模板视频预览">
        <div ref={container} className="template-preview-player absolute inset-0 size-full" />
        {sprites.length > 0 && canvas && <SpriteOverlay ref={overlay} clips={sprites} stageAspect={canvas.width / canvas.height} initialTime={time} />}
        {!ready && !failed && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-white/60"><Spinner className="size-6" /></div>}
        <Hint label="全屏预览"><Button type="button" variant="ghost" size="icon-xs" aria-label="全屏预览" onClick={() => { if (stage.current) void stage.current.requestFullscreen(); }} className="template-preview-fullscreen absolute bottom-2 right-2 rounded-[5px] p-0"><Maximize2 className="size-3" aria-hidden="true" /></Button></Hint>
      </div>
      <div className="template-preview-controls flex flex-wrap items-center gap-2.5 py-3">
        <Hint label={playing ? "暂停" : "播放"}><Button type="button" aria-label={playing ? "暂停" : "播放"} disabled={!ready || seeking} onClick={() => { if (playing) { cancelPlaybackAction.current?.(); player.current?.pause(); setStatus("预览已暂停"); } else { const current = player.current?.currentTime ?? 0; playAction.current?.(current >= duration ? 0 : current); } }} className="template-preview-play size-8 shrink-0 rounded-lg p-0">{playing ? <Pause className="size-4 fill-current" aria-hidden="true" /> : <Play className="ml-0.5 size-4 fill-current" aria-hidden="true" />}</Button></Hint>
        <Button type="button" variant="ghost" size="sm" disabled={!ready || seeking} onClick={() => playAction.current?.(0)} className="template-preview-restart h-8 px-2 text-[11px]">从头重播</Button>
        <input type="range" aria-label="预览进度" min={0} max={duration} step={0.1} value={Math.min(time, duration)} disabled={!ready} onChange={(event) => seekAction.current?.(Number(event.target.value))} className="template-preview-progress min-w-20 flex-1" style={{ "--preview-progress": `${duration > 0 ? time / duration * 100 : 0}%` } as CSSProperties} />
        <span className="min-w-20 text-right text-[11px] font-medium tabular-nums">{time.toFixed(1)} <span className="font-normal text-muted-foreground">/ {duration.toFixed(1)} 秒</span></span>
        <MasterVideoInput key={videoInputKey} media={media} onChange={onMediaChange} />
      </div>
      <p role={failed ? "alert" : "status"} className={failed ? "text-xs text-destructive" : ready ? "sr-only" : "text-[11px] text-muted-foreground"}>{status}</p>
      {failed && <Button type="button" variant="outline" onClick={() => setAttempt((value) => value + 1)}>重试预览</Button>}
      {notices.length > 0 && <ul aria-label="时间调整说明" className="space-y-1 text-xs text-muted-foreground">{notices.map((notice, index) => <li key={index}>{notice}</li>)}</ul>}
    </div></div>
    <div className="template-preview-timeline flex-1 px-4 pb-5 pt-4 lg:px-7"><div className="mx-auto max-w-[900px]"><div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><TimelineMark /><h2 className="text-[13px] font-semibold">时间轴</h2>{transition && <Button type="button" variant="ghost" size="sm" disabled={!ready || seeking} onClick={() => playAction.current?.(Math.max(0, transition.start - 1), Math.min(duration, transition.end + 1))} className="template-preview-transition h-7 px-2 text-[11px]">预览转场</Button>}</div><div className="flex items-center gap-3 text-[11px] tabular-nums text-muted-foreground"><span className="font-semibold text-foreground">{time.toFixed(1)} / {duration.toFixed(1)} 秒</span><span>{draft.tracks.length} 个对象</span></div></div><PreviewTimeline ref={track} rows={timelineRows} disabled={!ready} time={time} duration={duration} selectedId={selectedId} onSelect={onSelect} onRangeChange={onRangeChange} onSeek={(value) => seekAction.current?.(value)} /></div></div>
  </section>;
}
