/**
 * SDK 预览组件：管理单个播放器、串行更新时间线；授权在后台请求，IMS 渲染或载入时间线失败、卡住或 60 秒仍不能播放时降级为基础视频，
 * 基础视频同样由播放按钮、进度条和时间轴控制并驱动 AI 资产；卸载时清理订阅和异步任务。
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Maximize2, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { Spinner } from "@/components/ui/spinner";
import { textKey, type Draft, type EffectAsset, type PreviewMedia } from "./model";
import { CAN_PLAY_TIMEOUT_MS, deferLicenseRequest, loadSDK, readCatalog, waitForCanPlay, watchPlayer, watchSdkMedia, type CanPlayResult, type Player } from "./sdk";
import { BasicVideoPreview, type BasicVideoHandle } from "./BasicVideoPreview";
import { buildTimeline, buildPreviewRows, mergeSpriteRows, PREVIEW_CANVAS, remotionTexts, type SpriteClip } from "./timeline";
import { centeredPlayerTimeline } from "./centeredTransitions";
import { PreviewTimeline, type PreviewTimelineHandle } from "./PreviewTimeline";
import { SpriteOverlay, type SpriteOverlayHandle } from "./SpriteOverlay";
import type { ResolvedTimeline } from "./tracks";
import { MasterVideoInput } from "./MasterVideoInput";
import { debugLog, describe, diagnosticsEnabled, MEDIA_ERRORS, watchWindow } from "@/lib/diagnostics";
import { watchPlayback } from "./playbackStats";

/** 编辑状态由父组件持有；目录只在 SDK 初始化成功后回传。 */
interface Props {
  draft: Draft;
  media?: PreviewMedia;
  resolved?: ResolvedTimeline;
  mediaError?: string;
  onRetryMedia?: () => void;
  onMediaChange: (media: PreviewMedia) => void;
  videoInputKey: string;
  onCatalog: (catalog: EffectAsset[]) => void;
  selectedId?: string | null;
  selectionTargets?: ReadonlyMap<string, string>;
  onSelect?: (id: string) => void;
  videoOrder?: number[];
  sprites?: SpriteClip[];
  /** 在画面上拖动所选 AI 资产时回报新的内容中心坐标（画布像素）。 */
  onSpriteMove?: (id: string, x: number, y: number) => void;
}

/** 时间轴标记由三条片段和一条播放指针组成。 */
function TimelineMark() {
  return <span className="template-timeline-mark flex size-8 items-center justify-center rounded-lg" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" className="size-5"><rect x="2" y="7" width="8" height="4" rx="1" fill="currentColor" /><rect x="14" y="7" width="8" height="4" rx="1" fill="currentColor" /><rect x="5" y="15" width="13" height="4" rx="1" fill="currentColor" /><path d="M12 4v17m-2-18h4l-2 3-2-3Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg></span>;
}

/** 每次修改全量更新时间线并回到开头；串行处理，快速修改只应用最新草稿。 */
export function TemplatePreview({ draft, media, resolved, mediaError, onRetryMedia, onMediaChange, videoInputKey, onCatalog, selectedId, selectionTargets, onSelect, sprites = [], onSpriteMove, videoOrder = defaultVideoOrder }: Props) {
  const duration = resolved?.duration ?? 0;
  // 改由 Remotion 字效显示的文字变化时，SDK 需要重新应用时间线以隐藏或恢复 IMS 文字。
  const replacedTexts = [...remotionTexts(draft)].sort().join();
  const stage = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const player = useRef<Player | null>(null);
  const latest = useRef(draft);
  const latestMedia = useRef(media);
  const latestOrder = useRef(videoOrder);
  const latestResolved = useRef(resolved);
  const latestTexts = useRef(replacedTexts);
  const apply = useRef<(() => void) | null>(null);
  const catalogCallback = useRef(onCatalog);
  const playAction = useRef<((start: number) => void) | null>(null);
  const cancelPlaybackAction = useRef<(() => void) | null>(null);
  const seekAction = useRef<((time: number) => void) | null>(null);
  const track = useRef<PreviewTimelineHandle>(null);
  const overlay = useRef<SpriteOverlayHandle>(null);
  const basic = useRef<BasicVideoHandle>(null);
  // 基础视频最近一次显示到界面的时间（0.1 秒为单位）；切换基础视频时置为 -1，让第一次回报一定刷新界面。
  const basicDecisecond = useRef(-1);
  // 效果目录：SDK 加载前用随包目录（与工作区编辑用的相同），加载后换成 SDK 目录；时间轴行与 SDK 时间线共用。
  const [catalog, setCatalog] = useState<EffectAsset[]>(readCatalog);
  const latestCatalog = useRef(catalog);
  // 时间轴的对象与视频行、时间调整说明直接按当前草稿生成，不等 SDK 应用时间线：IMS 加载、出错或卡住时照常显示。
  const { rows, notices } = useMemo((): { rows: ReturnType<typeof buildPreviewRows>; notices: string[] } => {
    if (!media || !resolved) return { rows: [], notices: [] };
    try {
      const timeline = buildTimeline(draft, catalog, media, resolved, videoOrder);
      return { rows: buildPreviewRows(timeline), notices: timeline.notices };
    } catch {
      // 生成失败时 SDK 应用同一份时间线也会失败，原因在那里提示与记录。
      return { rows: [], notices: [] };
    }
  }, [draft, catalog, media, resolved, videoOrder]);
  const timelineRows = useMemo(() => mergeSpriteRows(rows, sprites).map((row) => ({ ...row,
    actions: row.actions.map((clip) => ({ ...clip, targetId: clip.targetId ? selectionTargets?.get(clip.targetId) ?? clip.targetId : undefined })),
  })), [rows, sprites, selectionTargets]);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("正在加载预览组件…");
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [basicFallback, setBasicFallback] = useState(false);
  const [basicReady, setBasicReady] = useState(false);
  // 切换基础视频的原因，显示在预览下方说明里。
  const [fallbackReason, setFallbackReason] = useState("");
  // 播放器内部加载失败的预览视频：元数据读取成功不代表 SDK 能播放（编码、跨域等），失败时不能只显示“已就绪”。
  const [mediaFailures, setMediaFailures] = useState<string[]>([]);
  const [seeking, setSeeking] = useState(false);
  const [time, setTime] = useState(0);
  const playing = status === "正在播放预览…";
  // 播放按钮、进度条与时间轴在 IMS 预览就绪或基础视频可播放时都可用。
  const controlsReady = ready || (basicFallback && basicReady);
  latest.current = draft;
  latestMedia.current = media;
  latestOrder.current = videoOrder;
  latestResolved.current = resolved;
  latestTexts.current = replacedTexts;
  latestCatalog.current = catalog;
  catalogCallback.current = onCatalog;

  useEffect(() => {
    let disposed = false;
    let active = false;
    let revision = 0;
    let instance: Player | null = null;
    let frame: HTMLIFrameElement | undefined;
    const controller = new AbortController();
    const transitionFrames = new Map<string, string>();
    let appliedTracks: Draft["tracks"] | undefined;
    let appliedMedia: PreviewMedia | undefined;
    let appliedOrder: number[] | undefined;
    let appliedTexts: string | undefined;
    let appliedResolved: typeof resolved;
    let subscription: { unsubscribe(): void } | undefined;
    let stopPlayerLog: (() => void) | undefined;
    let stopPlayback: (() => void) | undefined;
    let stopLicenseWatch: (() => void) | undefined;
    let canPlayController: AbortController | undefined;
    let basicRequested = false;
    let mediaFailed = false;
    let notifyMediaCreated: ((media: HTMLMediaElement) => void) | undefined;
    let seekTarget: number | null = null;
    let resumeAfterSeek = false;
    let seekFrame: number | undefined;
    let seekTimer: ReturnType<typeof setTimeout> | undefined;
    let applyTimer: ReturnType<typeof setTimeout> | undefined;
    let displayedDecisecond = 0;
    let playTimer: ReturnType<typeof setTimeout> | undefined;
    setReady(false);
    setFailed(false);
    setBasicFallback(false);
    setBasicReady(false);
    basicRequested = false;
    setMediaFailures([]);
    setStatus("正在加载预览组件…");
    if (!media) { setStatus("正在读取预览视频…"); return; }

    /** 取消尚未完成的定位与播放，暂停、更新和卸载共用此清理入口。 */
    const cancel = () => {
      seekTarget = null;
      resumeAfterSeek = false;
      clearTimeout(playTimer);
      clearTimeout(seekTimer);
      canPlayController?.abort();
      canPlayController = undefined;
      if (seekFrame !== undefined) cancelAnimationFrame(seekFrame);
      seekFrame = undefined;
      if (!disposed) setSeeking(false);
    };

    /**
     * SDK 渲染引擎不可用、出错或卡住时切换到基础视频，只停用 IMS 文字与特效，不阻断普通视频播放。
     * reason 显示在预览下方并写进调试日志，error 只写进日志（带调用栈）。
     */
    const switchToBasicVideo = (reason: string, error?: unknown) => {
      if (disposed || mediaFailed || basicRequested) return;
      basicRequested = true;
      cancel();
      instance?.pause();
      basicDecisecond.current = -1;
      setBasicFallback(true);
      setFallbackReason(reason);
      setReady(false);
      setFailed(false);
      setStatus("正在加载基础视频…");
      debugLog("模板预览", `切换基础视频预览：${reason}${error === undefined ? "" : `（${describe(error)}）`}`, "警告");
    };

    /** 定位超过等待时间时 SDK 已不能正常播放，切换基础视频，用户可重试 IMS 预览。 */
    const watchSeek = () => {
      setSeeking(true);
      clearTimeout(seekTimer);
      seekTimer = setTimeout(() => {
        debugLog("模板预览", `定位超时：10 秒内播放器没有确认定位到 ${seekTarget} 秒（当前 ${instance?.currentTime} 秒）`);
        switchToBasicVideo("10 秒内没有定位到播放位置");
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
            setStatus("正在播放预览…");
          }
        }, 0);
      } else setStatus("预览已暂停");
    };

    // 避免异步旧时间线覆盖新配置；卸载后不再更新 React 状态。
    const update = async () => {
      if (!instance || active || disposed || basicRequested) return;
      active = true;
      setReady(false);
      if (!mediaFailed) setFailed(false);
      let applied = revision;
      let canPlay = Promise.resolve<CanPlayResult>("cancelled");
      try {
        do {
          applied = revision;
          cancel();
          const currentDraft = latest.current;
          const currentMedia = latestMedia.current;
          const currentOrder = latestOrder.current;
          const currentTexts = latestTexts.current;
          const currentResolved = latestResolved.current;
          if (!currentMedia || !currentResolved) { instance.pause(); return; }
          const applyStarted = performance.now();
          const timeline = buildTimeline(currentDraft, latestCatalog.current, currentMedia, currentResolved, currentOrder);
          if (disposed) return;
          instance.pause();
          instance.aspectRatio = timeline.AspectRatio;
          setStatus("正在应用效果并加载媒体…");
          // 清空 SDK 缓存的旧时间线，确保文字更新后只显示当前内容。
          instance.timeline = {};
          // 生成转场画面与 SDK 载入时间线同样设上限：卡在这里时 canplay 的计时还没开始，不能一直停在加载中。
          clearTimeout(applyTimer);
          applyTimer = setTimeout(() => switchToBasicVideo(`${CAN_PLAY_TIMEOUT_MS / 1000} 秒内没有载入时间线`), CAN_PLAY_TIMEOUT_MS);
          const playback = await centeredPlayerTimeline(timeline, controller.signal, transitionFrames);
          if (disposed || basicRequested) return;
          const framesReady = performance.now();
          canPlayController = new AbortController();
          const timelineApplied = instance.setTimeline(playback);
          canPlay = waitForCanPlay(instance, timelineApplied, canPlayController.signal);
          await timelineApplied;
          clearTimeout(applyTimer);
          if (basicRequested) return;
          // 调试模式下记录明显变慢的修改：改参数后预览迟迟不更新时，区分转场画面生成与播放器载入时间线。
          const applyFinished = performance.now();
          if (diagnosticsEnabled() && applyFinished - applyStarted > 1500)
            debugLog("模板预览", `应用时间线用时 ${((applyFinished - applyStarted) / 1000).toFixed(1)} 秒（生成时间线与转场画面 ${((framesReady - applyStarted) / 1000).toFixed(1)} 秒，播放器载入 ${((applyFinished - framesReady) / 1000).toFixed(1)} 秒）`, "警告");
          if (!disposed && applied === revision) {
            appliedTracks = currentDraft.tracks;
            appliedMedia = currentMedia;
            appliedOrder = currentOrder;
            appliedTexts = currentTexts;
            appliedResolved = currentResolved;
          }
        } while (!disposed && applied !== revision);
        if (!disposed) {
          instance.currentTime = 0;
          displayedDecisecond = 0;
          setTime(0);
          track.current?.setTime(0);
          overlay.current?.setTime(0);
          // 只有 canplay 才证明播放节点已建好；超时或取消都不能把预览标为可播放。
          const readiness = await canPlay;
          if (!disposed && !basicRequested && applied === revision && readiness === "canplay" && !mediaFailed) {
            setReady(true);
            setStatus("预览已就绪，点击播放查看效果");
          } else if (!disposed && !basicRequested && applied === revision && readiness === "timeout" && !mediaFailed) {
            switchToBasicVideo(`${CAN_PLAY_TIMEOUT_MS / 1000} 秒内没有可以播放的画面`);
          }
        }
      } catch (error) {
        // 生成转场画面或载入时间线失败同样只停用 IMS 部分；素材本身无法播放时保留上面的逐个视频提示。
        if (!disposed && !basicRequested) switchToBasicVideo(`应用时间线失败：${error instanceof Error ? error.message : "未知错误"}`, error);
      } finally {
        clearTimeout(applyTimer);
        active = false;
        canPlayController = undefined;
        if (!disposed && applied !== revision) void update();
      }
    };
    apply.current = () => {
      if (basicRequested) return;
      // 初始化已应用相同输入时，延迟通知无需再次重置播放位置。
      if (appliedTracks === latest.current.tracks && appliedMedia === latestMedia.current && appliedOrder === latestOrder.current && appliedTexts === latestTexts.current && appliedResolved === latestResolved.current) return;
      revision++;
      void update();
    };
    // 工作区已读取真实媒体信息，每种尺寸使用独立 SDK 文档。画布尺寸固定，不等 IMS 加载（见 PREVIEW_CANVAS）。
    void Promise.resolve(media)
      .then(async (sources) => {
        if (disposed || !container.current) return;
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
        // SDK 在 iframe 里运行，它的报错与 console 不会冒泡到主窗口，需要单独监听。
        watchWindow(frame.contentWindow!, "模板预览 SDK");
        const target = frame.contentDocument!;
        // 渲染引擎等授权回应才初始化、请求失败后一直黑屏；须在 SDK 加载前把授权改为后台请求，授权结果不再决定是否降级。
        stopLicenseWatch = deferLicenseRequest(frame.contentWindow!);
        // SDK 的媒体元素不挂到文档上，加载失败由此记录并按视频序号提示（SDK 会在地址后追加 #t= 片段）。
        const sdkMedia = watchSdkMedia(target, (element) => {
          if (disposed || basicRequested) return;
          mediaFailed = true;
          setReady(false);
          setFailed(true);
          const url = (element.currentSrc || element.src).replace(/#.*$/, "");
          const index = sources.findIndex((item) => item.url === url);
          const reason = MEDIA_ERRORS[element.error?.code ?? 0] ?? "未知错误";
          const message = `${index >= 0 ? `视频 ${index + 1} ` : "预览素材"}无法在播放器中播放（${reason}），请检查视频编码与跨域访问配置后重试预览。`;
          setStatus("预览素材无法播放，请检查视频编码与跨域访问配置后重试预览。");
          setMediaFailures((items) => items.includes(message) ? items : [...items, message]);
        }, (element) => notifyMediaCreated?.(element));
        target.documentElement.style.height = "100%";
        target.body.style.cssText = "margin:0;height:100%;overflow:hidden";
        const surface = target.createElement("div");
        surface.style.cssText = "width:100%;height:100%";
        target.body.append(surface);
        const sdk = await loadSDK(target, controller.signal);
        if (disposed || basicRequested) return;
        instance = new sdk({
          container: surface,
          mode: "component",
          controls: true,
          locale: "zh-CN",
          licenseConfig: { rootDomain: "", licenseKey: "" },
          aspectRatio: `${PREVIEW_CANVAS.width}:${PREVIEW_CANVAS.height}`,
          // SDK 按 16:9 的包围区域计算场景尺寸；竖屏画布受高度限制，按高度给出即得到 1080×1920。
          maxCanvasConfig: { height: PREVIEW_CANVAS.height },
          getMediaInfo: async (id, _type, _origin, url) => url || id,
          getTimelineMaterials: async (materials) =>
            materials.map((item) => {
              if (typeof item.mediaId === "string" && item.mediaId.startsWith("data:image/"))
                return { ...item, image: { src: item.mediaId } };
              const video = sources.find((source) => source.url === item.mediaId);
              if (!video) throw new Error("预览素材缺少真实媒体信息");
              return { ...item, video: { duration: video.duration } };
            }),
        });
        // SDK 在初始化 Promise 中应用场景尺寸，等待该过程后设置 Timeline。
        await Promise.resolve();
        if (disposed) return;
        const sdkCatalog = readCatalog(sdk);
        latestCatalog.current = sdkCatalog;
        setCatalog(sdkCatalog);
        catalogCallback.current(sdkCatalog);
        player.current = instance;
        stopPlayerLog = watchPlayer(instance, (message) => switchToBasicVideo(`渲染引擎出错：${message}`));
        stopPlayback = watchPlayback(instance, sdkMedia, (listener) => {
          notifyMediaCreated = listener;
          return () => { if (notifyMediaCreated === listener) notifyMediaCreated = undefined; };
        });
        subscription = instance.event$.subscribe((event) => {
          // 切换基础视频后 SDK 可能仍在后台加载并发出事件，不能再改写基础视频驱动的时间。
          if (disposed || active || !instance || basicRequested) return;
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
          overlay.current?.setTime(current);
          // SDK 仍逐帧驱动播放控制与 AI 资产；界面时间和时间轴游标最多每 0.1 秒更新一次，
          // 时间轴组件每次移动游标都会整体重渲染，与 SDK 共用主线程，两处更新合并为一次渲染。
          const nextDecisecond = Math.min(
            (latestResolved.current?.duration ?? 0) * 10,
            Math.max(0, Math.round(current * 10)),
          );
          if (nextDecisecond !== displayedDecisecond) {
            displayedDecisecond = nextDecisecond;
            setTime(nextDecisecond / 10);
            track.current?.setTime(current);
          }
          if (current >= (latestResolved.current?.duration ?? 0)) {
            track.current?.setTime(current);
            setStatus("预览结束，点击播放可重播");
          }
        });
        void update();
      })
      .catch((error) => {
        if (!disposed) switchToBasicVideo(`播放器初始化失败：${error instanceof Error ? error.message : "未知错误"}`, error);
      });
    // 定位完成后播放；暂停、更新或卸载时取消尚未开始的播放。
    playAction.current = (start) => {
      if (!instance || active || disposed) return;
      cancel();
      seekTarget = start;
      resumeAfterSeek = true;
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
      const target = Math.min(latestResolved.current?.duration ?? 0, Math.max(0, value));
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
      clearTimeout(applyTimer);
      subscription?.unsubscribe();
      stopPlayerLog?.();
      stopPlayback?.();
      stopLicenseWatch?.();
      notifyMediaCreated = undefined;
      playAction.current = null;
      cancelPlaybackAction.current = null;
      seekAction.current = null;
      apply.current = null;
      player.current = null;
      instance?.destroy();
      frame?.remove();
    };
  }, [attempt, media]);

  useEffect(() => {
    const timer = window.setTimeout(() => apply.current?.(), 250);
    return () => window.clearTimeout(timer);
  }, [draft.tracks, media, videoOrder, resolved, replacedTexts]);

  /** 基础视频回报的全局时间：AI 资产逐帧跟随；界面时间和时间轴游标与 SDK 预览一样最多每 0.1 秒更新一次。 */
  const showBasicTime = (value: number) => {
    const next = Math.max(0, Math.min(duration, value));
    overlay.current?.setTime(next);
    const decisecond = Math.round(next * 10);
    if (decisecond === basicDecisecond.current) return;
    basicDecisecond.current = decisecond;
    setTime(decisecond / 10);
    track.current?.setTime(next);
  };

  /** 进度条与时间轴定位：基础视频直接定位，IMS 预览等 SDK 确认。 */
  const seekTo = (value: number) => {
    if (basicFallback) basic.current?.seek(value);
    else seekAction.current?.(value);
  };

  return <section aria-label="实时预览" className="flex min-h-full min-w-0 flex-col">
    <div className="template-preview-stage border-b px-4 pb-3 pt-4 lg:px-7 lg:pt-5"><div className="mx-auto max-w-[900px]">
      <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="template-live-dot size-1.5 rounded-full" aria-hidden="true" /><h2 className="text-[13px] font-semibold">实时预览</h2></div><span className="rounded-md border px-2 py-1 text-[11px] tabular-nums text-muted-foreground">{PREVIEW_CANVAS.width} × {PREVIEW_CANVAS.height}</span></div>
      <div ref={stage} className="template-preview-canvas relative mx-auto w-full overflow-hidden rounded-lg bg-black" style={{ aspectRatio: `${PREVIEW_CANVAS.width} / ${PREVIEW_CANVAS.height}`, maxWidth: `${48 * PREVIEW_CANVAS.width / PREVIEW_CANVAS.height}dvh`, "--preview-ratio": PREVIEW_CANVAS.width / PREVIEW_CANVAS.height } as CSSProperties} aria-label="模板视频预览">
        {/* 播放器与叠加层共用一个按画布比例排布的画面框：全屏时画布撑满屏幕，画面框仍按视频比例居中，字效与选择框随画面缩放而不是按整个屏幕排布。 */}
        <div className="template-preview-frame absolute inset-0 overflow-hidden">
          <div ref={container} className={`template-preview-player absolute inset-0 size-full ${basicFallback ? "invisible" : ""}`} />
          {basicFallback && media && resolved && <BasicVideoPreview ref={basic} media={media} resolved={resolved} order={videoOrder}
            onTime={showBasicTime}
            onReady={() => { setBasicReady(true); setStatus("基础视频已就绪，点击播放查看效果"); }}
            onPlayingChange={(value) => setStatus(value ? "正在播放预览…" : "预览已暂停")}
            onError={() => { setFailed(true); setStatus("基础视频预览加载失败，请检查视频编码与跨域访问配置后重试。"); }}
            onEnd={() => setStatus("预览结束，点击播放可重播")} />}
          {controlsReady && onSelect && draft.tracks.filter((item) => (item.target === "title" || item.target === "subtitle") && item.enabled !== false && !remotionTexts(draft).has(textKey(item.target, item.language))).map((item) => {
            const interval = resolved?.tracks[item.id];
            if (!interval || time < interval.start || time >= interval.end) return null;
            const role = item.target as "title" | "subtitle";
            return <button key={item.id} type="button" aria-label={`选择${item.language === "en" ? "英文" : "中文"}${role === "title" ? "标题" : "字幕"}`} aria-pressed={selectedId === item.id}
              className={`absolute -translate-x-1/2 rounded-sm border border-dashed hover:border-primary focus-visible:ring-2 focus-visible:ring-ring ${selectedId === item.id ? "border-primary" : "border-transparent"}`}
              style={{ left: `${item.editor[`${role}X`]}%`, top: `${item.editor[`${role}Y`]}%`, width: "90%", height: `${item.editor[`${role}Size`] * 1.5 / PREVIEW_CANVAS.height * 100}%`, minHeight: 12 }}
              onClick={() => onSelect(item.id)} />;
          })}
          {sprites.length > 0 && <SpriteOverlay ref={overlay} clips={sprites} stageAspect={PREVIEW_CANVAS.width / PREVIEW_CANVAS.height} initialTime={time} selectedId={selectedId} onMove={onSpriteMove} onSelect={onSelect} />}
        </div>
        {!controlsReady && !failed && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-white/60"><Spinner className="size-6" /></div>}
        <Hint label="全屏预览"><Button type="button" variant="ghost" size="icon-xs" aria-label="全屏预览" onClick={() => { if (stage.current) void stage.current.requestFullscreen(); }} className="template-preview-fullscreen absolute bottom-2 right-2 rounded-[5px] p-0"><Maximize2 className="size-3" aria-hidden="true" /></Button></Hint>
      </div>
      <div className="template-preview-controls flex flex-wrap items-center gap-2.5 py-3">
        <Hint label={playing ? "暂停" : "播放"}><Button type="button" aria-label={playing ? "暂停" : "播放"} disabled={!controlsReady || seeking} onClick={() => { if (basicFallback) { if (playing) basic.current?.pause(); else basic.current?.play(); } else if (playing) { cancelPlaybackAction.current?.(); player.current?.pause(); track.current?.setTime(player.current?.currentTime ?? 0); setStatus("预览已暂停"); } else { const current = player.current?.currentTime ?? 0; playAction.current?.(current >= duration ? 0 : current); } }} className="template-preview-play size-8 shrink-0 rounded-lg p-0">{playing ? <Pause className="size-4 fill-current" aria-hidden="true" /> : <Play className="ml-0.5 size-4 fill-current" aria-hidden="true" />}</Button></Hint>
        <Button type="button" variant="ghost" size="sm" disabled={!controlsReady || seeking} onClick={() => { if (basicFallback) basic.current?.play(0); else playAction.current?.(0); }} className="template-preview-restart h-8 px-2 text-[11px]">从头重播</Button>
        <input type="range" aria-label="预览进度" min={0} max={duration} step={0.1} value={Math.min(time, duration)} disabled={!controlsReady} onChange={(event) => seekTo(Number(event.target.value))} className="template-preview-progress min-w-20 flex-1" style={{ "--preview-progress": `${duration > 0 ? time / duration * 100 : 0}%` } as CSSProperties} />
        <span className="min-w-20 text-right text-[11px] font-medium tabular-nums">{time.toFixed(1)} <span className="font-normal text-muted-foreground">/ {duration.toFixed(1)} 秒</span></span>
        <MasterVideoInput key={videoInputKey} media={media} onChange={onMediaChange} />
      </div>
      <p role={failed || mediaError ? "alert" : "status"} className={failed || mediaError ? "text-xs text-destructive" : controlsReady ? "sr-only" : "text-[11px] text-muted-foreground"}>{mediaError || status}</p>
      {mediaError && !media && <Button type="button" variant="outline" onClick={onRetryMedia}>重试读取视频</Button>}
      {mediaFailures.length > 0 && <div role="alert" className="space-y-1 text-xs text-destructive">{mediaFailures.map((message) => <p key={message}>{message}</p>)}</div>}
      {(failed || mediaFailures.length > 0) && <Button type="button" variant="outline" onClick={() => setAttempt((value) => value + 1)}>重试预览</Button>}
      {basicFallback && <p className="text-[11px] text-muted-foreground">IMS 渲染暂不可用（{fallbackReason}），已切换基础视频预览：AI 资产照常显示，IMS 文字、转场、滤镜与特效暂不显示。</p>}
      {basicFallback && <Button type="button" variant="outline" onClick={() => setAttempt((value) => value + 1)}>重试 IMS 预览</Button>}
      {notices.length > 0 && <ul aria-label="时间调整说明" className="space-y-1 text-xs text-muted-foreground">{notices.map((notice, index) => <li key={index}>{notice}</li>)}</ul>}
    </div></div>
    <div className="template-preview-timeline flex-1 px-4 pb-5 pt-4 lg:px-7"><div className="mx-auto max-w-[900px]"><div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><TimelineMark /><h2 className="text-[13px] font-semibold">时间轴</h2></div><div className="flex items-center gap-3 text-[11px] tabular-nums text-muted-foreground"><span className="font-semibold text-foreground">{time.toFixed(1)} / {duration.toFixed(1)} 秒</span><span>{draft.tracks.length} 个对象</span></div></div><PreviewTimeline ref={track} rows={timelineRows} disabled={!controlsReady} time={time} duration={duration} selectedId={selectedId} onSelect={onSelect} onSeek={seekTo} />
    </div></div>
  </section>;
}

/** 默认顺序使用稳定引用，播放器只在实际排序变化时更新时间线。 */
const defaultVideoOrder = [0, 1, 2];
