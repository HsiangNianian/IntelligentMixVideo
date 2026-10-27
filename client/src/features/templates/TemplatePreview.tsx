/**
 * SDK 预览组件：管理单个播放器、串行更新时间线；授权在后台请求，IMS 渲染或载入时间线失败、卡住或 60 秒仍不能播放时降级为基础视频。
 * 播放位置由前端持有：IMS 就绪时由它推进，降级后由基础视频推进，二者都不可用时由前端时钟推进，换来源时接着当前位置与播放状态；
 * 播放按钮、进度条、时间轴、文字选择框与 AI 资产都只跟随这个位置，不等 IMS。卸载时清理订阅和异步任务。
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Maximize2, Pause, Play, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { Spinner } from "@/components/ui/spinner";
import { textKey, type Draft, type EffectAsset, type PreviewMedia } from "./model";
import { CAN_PLAY_TIMEOUT_MS, deferLicenseRequest, STALL_TIMEOUT_MS, loadSDK, readCatalog, waitForCanPlay, watchPlayer, watchSdkMedia, type CanPlayResult, type Player } from "./sdk";
import { BasicVideoPreview } from "./BasicVideoPreview";
import { PreviewClock, type PlaybackHandle } from "./previewClock";
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
  const [avatarSource, setAvatarSource] = useState(0);
  const latestAvatar = useRef(avatarSource);
  latestAvatar.current = avatarSource;
  const duration = resolved?.duration ?? 0;
  // 改由 Remotion 字效显示的文字变化时，SDK 需要重新应用时间线以隐藏或恢复 IMS 文字。
  const replacedTexts = [...remotionTexts(draft)].sort().join();
  const stage = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const latest = useRef(draft);
  const latestMedia = useRef(media);
  const latestOrder = useRef(videoOrder);
  const latestResolved = useRef(resolved);
  const latestTexts = useRef(replacedTexts);
  const apply = useRef<(() => void) | null>(null);
  const catalogCallback = useRef(onCatalog);
  // IMS 播放器的播放入口，播放器就绪后才使用。
  const ims = useRef<PlaybackHandle | null>(null);
  const track = useRef<PreviewTimelineHandle>(null);
  const overlay = useRef<SpriteOverlayHandle>(null);
  const basic = useRef<PlaybackHandle>(null);
  // 前端持有的准确播放位置（秒），由当前画面来源回报；换来源时新来源从这里接着走。
  const clockTime = useRef(0);
  // 最近一次显示到界面的时间（0.1 秒为单位）。
  const shownDecisecond = useRef(0);
  // 效果目录：SDK 加载前用随包目录（与工作区编辑用的相同），加载后换成 SDK 目录；时间轴行与 SDK 时间线共用。
  const [catalog, setCatalog] = useState<EffectAsset[]>(readCatalog);
  const latestCatalog = useRef(catalog);
  // 时间轴的对象与视频行、时间调整说明直接按当前草稿生成，不等 SDK 应用时间线：IMS 加载、出错或卡住时照常显示。
  const { rows, notices } = useMemo((): { rows: ReturnType<typeof buildPreviewRows>; notices: string[] } => {
    if (!media || !resolved) return { rows: [], notices: [] };
    try {
      const timeline = buildTimeline(draft, catalog, media, resolved, videoOrder, avatarSource);
      return { rows: buildPreviewRows(timeline), notices: timeline.notices };
    } catch {
      // 生成失败时 SDK 应用同一份时间线也会失败，原因在那里提示与记录。
      return { rows: [], notices: [] };
    }
  }, [draft, catalog, media, resolved, videoOrder, avatarSource]);
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
  const [playing, setPlaying] = useState(false);
  // 最新的播放状态：换画面来源时按它决定新来源是否接着播放，不等 React 渲染。
  const playingNow = useRef(false);
  // 有画面在播放：IMS 预览就绪，或已降级且基础视频可以播放。
  const videoReady = ready || (basicFallback && basicReady);
  // 播放按钮、进度条、时间轴与文字选择框只需要解析出的时间：前端时钟随时可用，不等 IMS 或基础视频。
  const controlsReady = duration > 0;
  // 当前推进播放位置的画面来源：IMS 就绪时用它，降级后用基础视频，二者都不可用时用前端时钟。
  const source = ready ? "ims" : basicFallback && basicReady ? "basic" : "clock";
  // PreviewClock 只创建一次；用 ref 让它的回调判断当前是否仍由前端时钟驱动，避免来源交接时覆盖新状态。
  const sourceRef = useRef<"ims" | "basic" | "clock">("clock");
  sourceRef.current = source;
  latest.current = draft;
  latestMedia.current = media;
  latestOrder.current = videoOrder;
  latestResolved.current = resolved;
  latestTexts.current = replacedTexts;
  latestCatalog.current = catalog;
  catalogCallback.current = onCatalog;

  /** 记录播放状态并刷新播放按钮。 */
  const changePlaying = (value: boolean) => {
    playingNow.current = value;
    setPlaying(value);
  };

  /**
   * 画面来源回报的全局时间：AI 资产逐帧跟随；界面时间和时间轴游标最多每 0.1 秒更新一次
   * （时间轴组件每次移动游标都会整体重渲染，与 SDK 共用主线程，两处更新合并为一次渲染）。只读写 ref 与状态，可在旧闭包里调用。
   */
  const showTime = (value: number) => {
    const next = Math.max(0, Math.min(latestResolved.current?.duration ?? 0, value));
    clockTime.current = next;
    overlay.current?.setTime(next);
    const decisecond = Math.round(next * 10);
    if (decisecond === shownDecisecond.current) return;
    shownDecisecond.current = decisecond;
    setTime(decisecond / 10);
    track.current?.setTime(next);
  };

  const [clock] = useState(() => new PreviewClock({
    duration: () => latestResolved.current?.duration ?? 0,
    onTime: (value) => showTime(value),
    onPlayingChange: (value) => {
      changePlaying(value);
      if (sourceRef.current === "clock") setStatus(value ? "正在播放预览…" : "预览已暂停");
    },
    onEnd: () => {
      changePlaying(false);
      if (sourceRef.current === "clock") setStatus("预览结束，点击播放可重播");
    },
  }));
  /** 当前画面来源的播放入口。 */
  const activePlayback = (): PlaybackHandle => (source === "ims" ? ims.current : source === "basic" ? basic.current : null) ?? clock;

  useEffect(() => {
    let disposed = false;
    let active = false;
    let revision = 0;
    let instance: Player | null = null;
    let frame: HTMLIFrameElement | undefined;
    const controller = new AbortController();
    const transitionFrames = new Map<string, string>();
    let appliedLayout: Draft["split_screen"];
    let previewMasks = new Map<string, { url: string; maskUrl: string }>();
    let appliedAvatar: number | undefined;
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
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
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
      clearTimeout(stallTimer);
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

    /**
     * IMS 播放中定时核对 SDK 时间：一段时间里没有前进说明 SDK 报告在播放却已卡住（例如系统 WebKitGTK 对 blob 视频一直等不到
     * canplaythrough），切换基础视频；前进了就从新位置接着核对。暂停、定位、修改与结束都会取消核对。
     */
    const watchStall = () => {
      clearTimeout(stallTimer);
      const from = instance?.currentTime;
      stallTimer = setTimeout(() => {
        if (!instance || disposed || basicRequested) return;
        // 停在结尾附近是播放结束，不算卡住。
        if (instance.currentTime >= (latestResolved.current?.duration ?? 0) - 0.1) return;
        if (instance.currentTime !== from) watchStall();
        else {
          debugLog("模板预览", `播放卡住：${STALL_TIMEOUT_MS / 1000} 秒内播放器时间停在 ${from} 秒`);
          switchToBasicVideo(`播放 ${STALL_TIMEOUT_MS / 1000} 秒没有前进`);
        }
      }, STALL_TIMEOUT_MS);
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
            changePlaying(true);
            setStatus("正在播放预览…");
            watchStall();
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
          const timeline = buildTimeline(currentDraft, latestCatalog.current, currentMedia, currentResolved, currentOrder, latestAvatar.current);
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
          previewMasks = new Map();
          for (const row of playback.VideoTracks) for (const clip of row.VideoTrackClips) {
            if (!("MaskVideoURL" in clip) || typeof clip.MaskVideoURL !== "string" || typeof clip.MediaURL !== "string") continue;
            // SDK 缓存媒体回调：把几何放进本次素材 ID，修改圆角或角色后才能重新读取蒙版。
            const id = `${clip.MediaURL}#imv-mask=${encodeURIComponent(clip.MaskVideoURL)}`;
            previewMasks.set(id, { url: clip.MediaURL, maskUrl: clip.MaskVideoURL });
            clip.MediaURL = id;
            delete clip.MaskVideoURL;
          }
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
            appliedLayout = currentDraft.split_screen;
            appliedAvatar = latestAvatar.current;
            appliedTracks = currentDraft.tracks;
            appliedMedia = currentMedia;
            appliedOrder = currentOrder;
            appliedTexts = currentTexts;
            appliedResolved = currentResolved;
          }
        } while (!disposed && applied !== revision);
        if (!disposed) {
          // 修改后从开头开始；界面时间由前端时钟持有，IMS 就绪时再交接到当前位置。
          instance.currentTime = 0;
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
      if (appliedLayout === latest.current.split_screen && appliedAvatar === latestAvatar.current && appliedTracks === latest.current.tracks && appliedMedia === latestMedia.current && appliedOrder === latestOrder.current && appliedTexts === latestTexts.current && appliedResolved === latestResolved.current) return;
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
          // 停下 SDK，由前端时钟接着推进播放位置。
          cancel();
          instance?.pause();
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
          // 动态媒体回调必须同时返回蒙版；5.2.2 在此路径不会读取片段上的 MaskVideoURL。
          getMediaInfo: async (id, _type, _origin, url) => {
            const source = url || id;
            return previewMasks.get(source) ?? source;
          },
          getTimelineMaterials: async (materials) =>
            materials.map((item) => {
              if (typeof item.mediaId === "string" && item.mediaId.startsWith("data:image/"))
                return { ...item, image: { src: item.mediaId } };
              const sourceUrl = previewMasks.get(String(item.mediaId))?.url ?? item.mediaId;
              const video = sources.find((source) => source.url === sourceUrl);
              if (!video) throw new Error("预览素材缺少真实媒体信息");
              return { ...item, video: { duration: video.duration, width: video.width, height: video.height } };
            }),
        });
        // SDK 在初始化 Promise 中应用场景尺寸，等待该过程后设置 Timeline。
        await Promise.resolve();
        if (disposed) return;
        const sdkCatalog = readCatalog(sdk);
        latestCatalog.current = sdkCatalog;
        setCatalog(sdkCatalog);
        catalogCallback.current(sdkCatalog);
        stopPlayerLog = watchPlayer(instance, (message) => switchToBasicVideo(`渲染引擎出错：${message}`));
        stopPlayback = watchPlayback(instance, sdkMedia, (listener) => {
          notifyMediaCreated = listener;
          return () => { if (notifyMediaCreated === listener) notifyMediaCreated = undefined; };
        });
        subscription = instance.event$.subscribe((event) => {
          // 只有 IMS 就绪后才由 SDK 推进播放位置：加载与应用修改时、素材无法播放或已切换基础视频后，SDK 仍可能发出事件，不能改写前端时间。
          if (disposed || active || !instance || basicRequested || mediaFailed) return;
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
          showTime(current);
          if (current >= (latestResolved.current?.duration ?? 0)) {
            clearTimeout(stallTimer);
            track.current?.setTime(current);
            changePlaying(false);
            setStatus("预览结束，点击播放可重播");
          }
        });
        void update();
      })
      .catch((error) => {
        if (!disposed) switchToBasicVideo(`播放器初始化失败：${error instanceof Error ? error.message : "未知错误"}`, error);
      });
    // 定位完成后播放；暂停、更新或卸载时取消尚未开始的播放。
    const play = (start: number) => {
      if (!instance || active || disposed) return;
      cancel();
      changePlaying(false);
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
    // 输入立即更新游标，一次浏览器绘制只提交最新定位；保持暂停并等待 SDK 确认。
    const seek = (value: number) => {
      if (!instance || active || disposed || !Number.isFinite(value)) return;
      const wasSeeking = seekTarget !== null;
      cancel();
      instance.pause();
      changePlaying(false);
      const target = Math.min(latestResolved.current?.duration ?? 0, Math.max(0, value));
      seekTarget = target;
      showTime(target);
      track.current?.setTime(target);
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
    ims.current = {
      play: (from) => {
        const current = instance?.currentTime ?? 0;
        play(from ?? (current >= (latestResolved.current?.duration ?? 0) ? 0 : current));
      },
      pause: () => {
        cancel();
        instance?.pause();
        changePlaying(false);
        setStatus("预览已暂停");
        // 游标停在播放器的准确位置，不按 0.1 秒取整。
        if (instance) { showTime(instance.currentTime); track.current?.setTime(instance.currentTime); }
      },
      seek,
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
      ims.current = null;
      apply.current = null;
      instance?.destroy();
      frame?.remove();
    };
  }, [attempt, media]);

  useEffect(() => {
    const timer = window.setTimeout(() => apply.current?.(), 250);
    return () => window.clearTimeout(timer);
  }, [draft.tracks, draft.split_screen, avatarSource, media, videoOrder, resolved, replacedTexts]);

  // 修改模板、视频或排列后回到开头并暂停（IMS 播放器随后重新应用时间线），各画面来源一致。
  useEffect(() => {
    activePlayback().pause();
    clock.seek(0);
  }, [media, videoOrder, resolved, draft.split_screen, avatarSource]);

  // 换画面来源（IMS 就绪或开始应用修改、切换基础视频、基础视频可以播放）时交接：新来源从当前位置接着走，
  // 原来在播放就接着播放，AI 资产与时间轴不跳回开头，播放也不因 IMS 就绪或降级中断。
  useEffect(() => {
    const at = clockTime.current;
    const resume = playingNow.current;
    if (source !== "clock") clock.pause();
    if (resume) activePlayback().play(at);
    else if (at > 0) activePlayback().seek(at);
  }, [source]);

  useEffect(() => () => clock.pause(), [clock]);

  return <section aria-label="实时预览" className="flex min-h-full min-w-0 flex-col">
    <div className="template-preview-stage border-b px-4 pb-3 pt-4 lg:px-7 lg:pt-5"><div className="mx-auto max-w-[900px]">
      <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="template-live-dot size-1.5 rounded-full" aria-hidden="true" /><h2 className="text-[13px] font-semibold">实时预览</h2></div><div className="flex items-center gap-2"><span className="rounded-md border px-2 py-1 text-[11px] tabular-nums text-muted-foreground">{PREVIEW_CANVAS.width} × {PREVIEW_CANVAS.height}</span><Hint label="刷新预览"><Button type="button" variant="ghost" size="icon-xs" aria-label="刷新预览" onClick={() => setAttempt((value) => value + 1)} className="rounded-[5px] p-0"><RotateCw className="size-3" aria-hidden="true" /></Button></Hint></div></div>
      {draft.split_screen?.enabled && <label className="mb-3 flex flex-wrap items-center gap-2 text-xs">数字人预览来源<select aria-label="数字人预览来源" value={avatarSource} onChange={(event) => setAvatarSource(Number(event.target.value))} className="rounded-md border bg-background px-2 py-1">{[0, 1, 2].map((index) => <option key={index} value={index}>视频 {index + 1}</option>)}</select><span className="text-muted-foreground">布局示例，短视频循环播放</span></label>}
      <div ref={stage} className="template-preview-canvas relative mx-auto w-full overflow-hidden rounded-lg bg-black" style={{ aspectRatio: `${PREVIEW_CANVAS.width} / ${PREVIEW_CANVAS.height}`, maxWidth: `${48 * PREVIEW_CANVAS.width / PREVIEW_CANVAS.height}dvh`, "--preview-ratio": PREVIEW_CANVAS.width / PREVIEW_CANVAS.height } as CSSProperties} aria-label="模板视频预览">
        {/* 播放器与叠加层共用一个按画布比例排布的画面框：全屏时画布撑满屏幕，画面框仍按视频比例居中，字效与选择框随画面缩放而不是按整个屏幕排布。 */}
        <div className="template-preview-frame absolute inset-0 overflow-hidden">
          <div ref={container} className={`template-preview-player absolute inset-0 size-full ${basicFallback ? "invisible" : ""}`} />
          {basicFallback && media && resolved && <BasicVideoPreview ref={basic} media={media} resolved={resolved} order={videoOrder} layout={draft.split_screen} avatarSource={avatarSource}
            onTime={(value) => { if (source === "basic") showTime(value); }}
            onReady={() => { setBasicReady(true); setStatus("基础视频已就绪，点击播放查看效果"); }}
            onPlayingChange={(value) => { if (source !== "basic") return; changePlaying(value); setStatus(value ? "正在播放预览…" : "预览已暂停"); }}
            onError={() => { setFailed(true); setStatus("基础视频预览加载失败，请检查视频编码与跨域访问配置后重试。"); }}
            onEnd={() => { if (source !== "basic") return; changePlaying(false); setStatus("预览结束，点击播放可重播"); }} />}
          {controlsReady && onSelect && draft.tracks.filter((item) => (item.target === "title" || item.target === "subtitle") && item.enabled !== false && !remotionTexts(draft).has(textKey(item.target, item.language))).map((item) => {
            const interval = resolved?.tracks[item.id];
            if (!interval || time < interval.start || time >= interval.end) return null;
            const role = item.target as "title" | "subtitle";
            const point = draft.split_screen?.enabled && role === "subtitle" ? draft.split_screen[item.language === "en" ? "subtitleEn" : "subtitle"] : { x: item.editor[`${role}X`], y: item.editor[`${role}Y`] };
            return <button key={item.id} type="button" aria-label={`选择${item.language === "en" ? "英文" : "中文"}${role === "title" ? "标题" : "字幕"}`} aria-pressed={selectedId === item.id}
              className={`absolute -translate-x-1/2 rounded-sm border border-dashed hover:border-primary focus-visible:ring-2 focus-visible:ring-ring ${selectedId === item.id ? "border-primary" : "border-transparent"}`}
              style={{ left: `${point.x}%`, top: `${point.y}%`, width: "90%", height: `${item.editor[`${role}Size`] * 1.5 / PREVIEW_CANVAS.height * 100}%`, minHeight: 12 }}
              onClick={() => onSelect(item.id)} />;
          })}
          {sprites.length > 0 && <SpriteOverlay ref={overlay} clips={sprites} stageAspect={PREVIEW_CANVAS.width / PREVIEW_CANVAS.height} initialTime={time} selectedId={selectedId} onMove={onSpriteMove} onSelect={onSelect} />}
        </div>
        {!videoReady && !failed && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-white/60"><Spinner className="size-6" /></div>}
        <Hint label="全屏预览"><Button type="button" variant="ghost" size="icon-xs" aria-label="全屏预览" onClick={() => { if (stage.current) void stage.current.requestFullscreen(); }} className="template-preview-fullscreen absolute bottom-2 right-2 rounded-[5px] p-0"><Maximize2 className="size-3" aria-hidden="true" /></Button></Hint>
      </div>
      <div className="template-preview-controls flex flex-wrap items-center gap-2.5 py-3">
        <Hint label={playing ? "暂停" : "播放"}><Button type="button" aria-label={playing ? "暂停" : "播放"} disabled={!controlsReady || seeking} onClick={() => { if (playing) activePlayback().pause(); else activePlayback().play(); }} className="template-preview-play size-8 shrink-0 rounded-lg p-0">{playing ? <Pause className="size-4 fill-current" aria-hidden="true" /> : <Play className="ml-0.5 size-4 fill-current" aria-hidden="true" />}</Button></Hint>
        <Button type="button" variant="ghost" size="sm" disabled={!controlsReady || seeking} onClick={() => activePlayback().play(0)} className="template-preview-restart h-8 px-2 text-[11px]">从头重播</Button>
        <input type="range" aria-label="预览进度" min={0} max={duration} step={0.1} value={Math.min(time, duration)} disabled={!controlsReady} onChange={(event) => activePlayback().seek(Number(event.target.value))} className="template-preview-progress min-w-20 flex-1" style={{ "--preview-progress": `${duration > 0 ? time / duration * 100 : 0}%` } as CSSProperties} />
        <span className="min-w-20 text-right text-[11px] font-medium tabular-nums">{time.toFixed(1)} <span className="font-normal text-muted-foreground">/ {duration.toFixed(1)} 秒</span></span>
        <MasterVideoInput key={videoInputKey} media={media} onChange={onMediaChange} />
      </div>
      <p role={failed || mediaError ? "alert" : "status"} className={failed || mediaError ? "text-xs text-destructive" : videoReady ? "sr-only" : "text-[11px] text-muted-foreground"}>{mediaError || status}</p>
      {mediaError && !media && <Button type="button" variant="outline" onClick={onRetryMedia}>重试读取视频</Button>}
      {mediaFailures.length > 0 && <div role="alert" className="space-y-1 text-xs text-destructive">{mediaFailures.map((message) => <p key={message}>{message}</p>)}</div>}
      {(failed || mediaFailures.length > 0) && <Button type="button" variant="outline" onClick={() => setAttempt((value) => value + 1)}>重试预览</Button>}
      {basicFallback && <p className="text-[11px] text-muted-foreground">IMS 渲染暂不可用（{fallbackReason}），已切换基础视频预览：AI 资产照常显示，IMS 文字、转场、滤镜与特效暂不显示。</p>}
      {basicFallback && <Button type="button" variant="outline" onClick={() => setAttempt((value) => value + 1)}>重试 IMS 预览</Button>}
      {notices.length > 0 && <ul aria-label="时间调整说明" className="space-y-1 text-xs text-muted-foreground">{notices.map((notice, index) => <li key={index}>{notice}</li>)}</ul>}
    </div></div>
    <div className="template-preview-timeline flex-1 px-4 pb-5 pt-4 lg:px-7"><div className="mx-auto max-w-[900px]"><div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><TimelineMark /><h2 className="text-[13px] font-semibold">时间轴</h2></div><div className="flex items-center gap-3 text-[11px] tabular-nums text-muted-foreground"><span className="font-semibold text-foreground">{time.toFixed(1)} / {duration.toFixed(1)} 秒</span><span>{draft.tracks.length} 个对象</span></div></div><PreviewTimeline ref={track} rows={timelineRows} disabled={!controlsReady} time={time} duration={duration} selectedId={selectedId} onSelect={onSelect} onSeek={(value) => activePlayback().seek(value)} />
    </div></div>
  </section>;
}

/** 默认顺序使用稳定引用，播放器只在实际排序变化时更新时间线。 */
const defaultVideoOrder = [0, 1, 2];
