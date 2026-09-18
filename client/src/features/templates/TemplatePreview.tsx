/** SDK 预览组件：管理单个播放器、串行更新时间线，卸载时清理订阅和异步任务。 */
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { Draft, EffectAsset } from "./model";
import { loadSDK, loadPreviewFont, readCatalog, type Player } from "./sdk";
import { buildTimeline } from "./timeline";

/** 编辑状态由父组件持有；目录只在 SDK 初始化成功后回传。 */
interface Props {
  draft: Draft;
  onCatalog: (catalog: EffectAsset[]) => void;
}

/** 每次修改全量更新时间线并回到开头；串行处理，快速修改只应用最新草稿。 */
export function TemplatePreview({ draft, onCatalog }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const player = useRef<Player | null>(null);
  const latest = useRef(draft);
  const apply = useRef<(() => void) | null>(null);
  const catalogCallback = useRef(onCatalog);
  const playAction = useRef<((start: number, end?: number) => void) | null>(null);
  const cancelPlaybackAction = useRef<(() => void) | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("正在加载预览组件…");
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [time, setTime] = useState(0);
  latest.current = draft;
  catalogCallback.current = onCatalog;

  useEffect(() => {
    let disposed = false;
    let active = false;
    let revision = 0;
    let instance: Player | null = null;
    let catalog: EffectAsset[] = [];
    let subscription: { unsubscribe(): void } | undefined;
    let seekTarget: number | null = null;
    let transitionEnd = 0;
    let displayedDecisecond = 0;
    let playTimer: ReturnType<typeof setTimeout> | undefined;
    setReady(false);
    setFailed(false);
    setStatus("正在加载预览组件…");

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
          seekTarget = null;
          transitionEnd = 0;
          clearTimeout(playTimer);
          const timeline = buildTimeline(latest.current, catalog);
          if (disposed) return;
          instance.pause();
          setStatus("正在应用效果并加载媒体…");
          await instance.setTimeline(timeline);
        } while (!disposed && applied !== revision);
        if (!disposed) {
          instance.currentTime = 0;
          displayedDecisecond = 0;
          setTime(0);
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
            materials.map((item) => ({ ...item, video: { duration: 14 } })),
        });
        catalog = readCatalog(sdk);
        catalogCallback.current(catalog);
        player.current = instance;
        subscription = instance.event$.subscribe((event) => {
          if (disposed || !instance || event.type !== "render") return;
          const current = instance.currentTime;
          // SDK 仍逐帧驱动播放控制，界面时间最多每 0.1 秒渲染一次。
          const nextDecisecond = Math.min(
            100,
            Math.max(0, Math.round(current * 10)),
          );
          if (nextDecisecond !== displayedDecisecond) {
            displayedDecisecond = nextDecisecond;
            setTime(nextDecisecond / 10);
          }
          if (seekTarget !== null && Math.abs(current - seekTarget) < 0.15) {
            seekTarget = null;
            // 等定位结束，避免 SDK 的异步暂停覆盖紧接着的播放调用。
            playTimer = setTimeout(() => {
              if (!disposed) {
                instance?.play();
                setStatus(transitionEnd ? "正在预览转场…" : "正在播放预览…");
              }
            }, 0);
          } else if (transitionEnd && current >= transitionEnd) {
            instance.pause();
            transitionEnd = 0;
            setStatus("转场预览结束");
          } else if (current >= 10) {
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
      clearTimeout(playTimer);
      instance.pause();
      seekTarget = start;
      transitionEnd = end;
      setStatus("正在定位播放位置…");
      instance.currentTime = start;
    };
    cancelPlaybackAction.current = () => {
      seekTarget = null;
      transitionEnd = 0;
      clearTimeout(playTimer);
    };
    return () => {
      disposed = true;
      clearTimeout(playTimer);
      subscription?.unsubscribe();
      playAction.current = null;
      cancelPlaybackAction.current = null;
      apply.current = null;
      player.current = null;
      if (!active) instance?.destroy();
    };
  }, [attempt]);

  useEffect(() => {
    const timer = window.setTimeout(() => apply.current?.(), 250);
    return () => window.clearTimeout(timer);
  }, [draft.editor, draft.transition_duration_seconds]);

  return (
    <Card className="min-w-0 gap-4 p-5 md:sticky md:top-6">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">实时预览</h2>
        <span className="text-xs text-muted-foreground">
          16:9 · {time.toFixed(1)} / 10 秒
        </span>
      </div>
      <div
        ref={container}
        className="aspect-video w-full overflow-hidden rounded-lg bg-foreground"
        aria-label="模板视频预览"
      />
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!ready}
          onClick={() => playAction.current?.(0)}
        >
          播放 / 重播
        </Button>
        <Button
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
          variant="outline"
          disabled={!ready || !draft.editor.transition}
          onClick={() =>
            playAction.current?.(
              4,
              Math.min(10, 5 + draft.transition_duration_seconds + 1),
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
      <p className="text-xs text-muted-foreground">
        示例文字仅用于模板预览。首次加载需要联网获取 SDK、字体和视频；请通过
        localhost 打开，并开启浏览器硬件加速。
      </p>
    </Card>
  );
}
