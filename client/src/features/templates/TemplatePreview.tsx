/** SDK 预览组件：管理单个播放器、串行更新时间线，卸载时清理订阅和异步任务。 */
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Draft, EffectAsset, MasterVideo } from "./model";
import { loadSDK, loadPreviewFont, readCatalog, type Player } from "./sdk";
import { buildTimeline, buildPreviewRows } from "./timeline";
import { PreviewTimeline, type PreviewTimelineHandle } from "./PreviewTimeline";
import { previewDuration } from "./tracks";

/** 编辑状态由父组件持有；目录只在 SDK 初始化成功后回传。 */
interface Props {
  draft: Draft;
  media?: MasterVideo;
  onCatalog: (catalog: EffectAsset[]) => void;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onRangeChange?: (id: string, start: number, end: number) => void;
}

/** 每次修改全量更新时间线并回到开头；串行处理，快速修改只应用最新草稿。 */
export function TemplatePreview({ draft, media, onCatalog, selectedId, onSelect, onRangeChange }: Props) {
  const duration = Math.max(0, previewDuration(draft, media));
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
  const [rows, setRows] = useState<ReturnType<typeof buildPreviewRows>>([]);
  const [notices, setNotices] = useState<string[]>([]);
  const transition = rows.flatMap((row) => row.actions).find((item) => item.effectId === "transition");
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("正在加载预览组件…");
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [time, setTime] = useState(0);
  latest.current = draft;
  latestMedia.current = media;
  catalogCallback.current = onCatalog;

  useEffect(() => {
    let disposed = false;
    let active = false;
    let revision = 0;
    let instance: Player | null = null;
    let catalog: EffectAsset[] = [];
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
          const timeline = buildTimeline(latest.current, catalog, latestMedia.current);
          if (disposed) return;
          instance.pause();
          instance.aspectRatio = timeline.AspectRatio;
          setStatus("正在应用效果并加载媒体…");
          const { previewRows: _rows, notices: adjustments, ...sdkTimeline } = timeline;
          await instance.setTimeline(sdkTimeline);
          if (!disposed && applied === revision) {
            setRows(buildPreviewRows(timeline));
            setNotices(adjustments);
          }
        } while (!disposed && applied !== revision);
        if (!disposed) {
          instance.currentTime = 0;
          displayedDecisecond = 0;
          setTime(0);
          track.current?.setTime(0);
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
        if (disposed) instance?.destroy();
        else if (applied !== revision) void update();
      }
    };
    apply.current = () => {
      revision++;
      void update();
    };
    void Promise.all([loadSDK(), loadPreviewFont()])
      .then(([sdk]) => {
        if (disposed || !container.current) return;
        instance = new sdk({
          container: container.current,
          mode: "component",
          controls: true,
          locale: "zh-CN",
          licenseConfig: { rootDomain: "", licenseKey: "" },
          aspectRatio: "16:9",
          getMediaInfo: async (id, _type, _origin, url) => url || id,
          getTimelineMaterials: async (materials) =>
            materials.map((item) => ({ ...item, video: { duration: latestMedia.current?.duration ?? 14 } })),
        });
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
    // 一次浏览器绘制只提交最新定位，拖动期间始终暂停，最终位置等待 SDK 帧事件。
    seekAction.current = (value) => {
      if (!instance || active || disposed || !Number.isFinite(value)) return;
      const wasSeeking = seekTarget !== null;
      cancel();
      instance.pause();
      const target = Math.min(previewDuration(latest.current, latestMedia.current), Math.max(0, value));
      seekTarget = target;
      setStatus("正在定位播放位置…");
      watchSeek();
      seekFrame = requestAnimationFrame(() => {
        seekFrame = undefined;
        if (!instance || disposed) return;
        if (!wasSeeking && Math.abs(instance.currentTime - target) < 0.001) {
          finishSeek();
          displayedDecisecond = Math.round(target * 10);
          setTime(displayedDecisecond / 10);
          track.current?.setTime(target);
        } else instance.currentTime = target;
      });
    };
    return () => {
      disposed = true;
      cancel();
      subscription?.unsubscribe();
      playAction.current = null;
      cancelPlaybackAction.current = null;
      seekAction.current = null;
      apply.current = null;
      player.current = null;
      if (!active) instance?.destroy();
    };
  }, [attempt]);

  useEffect(() => {
    const timer = window.setTimeout(() => apply.current?.(), 250);
    return () => window.clearTimeout(timer);
  }, [draft.editor, draft.transition_duration_seconds, draft.tracks, media]);

  return (
    <section aria-label="实时预览" className="min-w-0 space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">实时预览</h2>
        <span className="text-xs text-muted-foreground">
          {media ? `${media.width}:${media.height}` : "16:9"} · {time.toFixed(1)} / {Number(duration.toFixed(2))} 秒
        </span>
      </div>
      <div
        ref={container}
        className="aspect-video w-full overflow-hidden rounded-lg bg-foreground"
        style={media ? { aspectRatio: `${media.width} / ${media.height}` } : undefined}
        aria-label="模板视频预览"
      />
      <PreviewTimeline ref={track} rows={rows} disabled={!ready} time={time} duration={duration} selectedId={selectedId} onSelect={onSelect} onRangeChange={onRangeChange} onSeek={(value) => seekAction.current?.(value)} />
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={!ready || seeking}
          onClick={() => {
            const current = player.current?.currentTime ?? 0;
            playAction.current?.(current >= duration ? 0 : current);
          }}
        >
          播放
        </Button>
        <Button type="button" variant="outline" disabled={!ready || seeking} onClick={() => playAction.current?.(0)}>
          从头重播
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!ready}
          onClick={() => {
            cancelPlaybackAction.current?.();
            player.current?.pause();
            setStatus("预览已暂停");
          }}
        >
          暂停
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!ready || seeking || (draft.tracks ? !transition : !draft.editor.transition)}
          onClick={() =>
            playAction.current?.(
              Math.max(0, (transition?.start ?? 5) - 1),
              Math.min(duration, (transition?.end ?? 5 + draft.transition_duration_seconds) + 1),
            )
          }
        >
          预览转场
        </Button>
      </div>
      <p
        role={failed ? "alert" : "status"}
        className={
          failed ? "text-sm text-destructive" : "text-sm text-muted-foreground"
        }
      >
        {status}
      </p>
      {failed && (
        <Button
          type="button"
          variant="outline"
          onClick={() =>
            player.current
              ? apply.current?.()
              : setAttempt((value) => value + 1)
          }
        >
          重试预览
        </Button>
      )}
      {notices.length > 0 && <ul aria-label="时间调整说明" className="space-y-1 text-xs text-muted-foreground">{notices.map((notice, index) => <li key={index}>{notice}</li>)}</ul>}
      <p className="text-xs text-muted-foreground">
        示例文字仅用于模板预览。首次加载需要联网获取 SDK、字体和视频；请通过
        localhost 打开，并开启浏览器硬件加速。
      </p>
    </section>
  );
}
