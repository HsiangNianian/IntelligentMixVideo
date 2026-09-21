/** 母版与特效轨道编辑；SDK 驱动游标，特效变更回传草稿，卸载取消缩略图任务。 */
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { Timeline, type TimelineState } from "@xzdarcy/react-timeline-editor";
import { Button } from "@/components/ui/button";
import type { buildPreviewRows, PreviewClip } from "./timeline";
import { loadThumbnails } from "./thumbnails";
import "@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css";
import "./preview-timeline.css";

/** 父组件仅推送播放时间，轨道不启动自身播放计时。 */
export interface PreviewTimelineHandle {
  setTime(time: number): void;
}

/** 轨道消费成功应用的数据；定位统一交给持有 SDK 的父组件处理。 */
interface Props {
  ref: Ref<PreviewTimelineHandle>;
  rows: ReturnType<typeof buildPreviewRows>;
  disabled: boolean;
  time: number;
  onSeek: (time: number) => void;
  duration?: number;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onRangeChange?: (id: string, start: number, end: number) => void;
}

/** 显示源素材缩略图，错误独立提示并允许重试；缓存随预览组件释放。 */
function ClipThumbnails({ clip, cache }: { clip: PreviewClip; cache: Map<string, string> }) {
  const [images, setImages] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setImages([]);
    setError("");
    void loadThumbnails(clip, controller.signal, cache).then(
      (result) => { if (!controller.signal.aborted) setImages(result); },
      (reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "缩略图生成失败");
      },
    );
    return () => controller.abort();
  }, [clip.url, clip.sourceIn, clip.sourceOut, cache, attempt]);
  return (
    <div className="relative h-full overflow-hidden rounded border border-primary/40 bg-muted" aria-label={`${clip.label}：${clip.start}～${clip.end} 秒`}>
      <div className="flex h-full" aria-hidden="true">
        {images.map((src, index) => <img key={index} src={src} alt="" draggable={false} className="h-full min-w-0 flex-1 object-cover" />)}
      </div>
      <span className="absolute left-1 top-1 rounded bg-background/90 px-1 text-xs text-foreground">{clip.label}</span>
      {!images.length && !error && <span className="absolute bottom-1 left-1 text-xs text-muted-foreground">正在加载缩略图…</span>}
      {error && <div className="absolute inset-x-1 bottom-0 flex items-center gap-1 bg-background/95 text-xs">
        <span role="status" className="truncate" title={error}>{error}</span>
        <Button type="button" size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); setAttempt((value) => value + 1); }}>重试缩略图</Button>
      </div>}
      {clip.transition && <div
        className="pointer-events-none absolute inset-y-0 border-x-2 border-primary bg-primary/25"
        style={{ left: `${100 * (clip.transition.start - clip.start) / (clip.end - clip.start)}%`, width: `${100 * (clip.transition.end - clip.transition.start) / (clip.end - clip.start)}%` }}
        title={`转场：${clip.transition.start}～${clip.transition.end} 秒`}
      />}
    </div>
  );
}

/** 轨道宽度随容器调整；鼠标拖动期间忽略播放器旧时间，键盘支持 0.1 秒定位。 */
export function PreviewTimeline({ ref, rows, disabled, time, onSeek, duration = 10, selectedId, onSelect, onRangeChange }: Props) {
  const timeline = useRef<TimelineState>(null);
  const container = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const cache = useRef(new Map<string, string>());
  const [width, setWidth] = useState(400);
  const [editableRows, setEditableRows] = useState(() => structuredClone(rows));
  // 时间轴组件会原地修改输入，编辑副本与成功应用的 SDK 数据分别持有。
  useEffect(() => { setEditableRows(structuredClone(rows)); }, [rows]);
  useImperativeHandle(ref, () => ({
    setTime(value) { if (!dragging.current) timeline.current?.setTime(value); },
  }), []);
  useEffect(() => {
    const element = container.current!;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    setWidth(element.clientWidth);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (disabled) dragging.current = false;
  }, [disabled]);
  /** 所有定位入口限制到当前母版范围。 */
  function seek(value: number) {
    if (disabled) return;
    const position = Math.min(duration, Math.max(0, value));
    timeline.current?.setTime(position);
    onSeek(position);
  }
  return (
    <div ref={container} className="preview-timeline min-w-0 space-y-2" role="group" aria-label="视频轨道">
      <div className="flex justify-between text-xs text-muted-foreground"><span>视频轨道</span><span>点击刻度或拖动游标定位</span></div>
      <div
        className="overflow-hidden rounded-md border focus-visible:outline-2 focus-visible:outline-ring"
        role="slider"
        aria-label="预览播放位置"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={time}
        aria-valuetext={`${time.toFixed(1)} 秒`}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        inert={disabled}
        onKeyDown={(event) => {
          const position = timeline.current?.getTime() ?? time;
          const target = { ArrowLeft: position - 0.1, ArrowRight: position + 0.1, Home: 0, End: duration }[event.key];
          if (target !== undefined) { event.preventDefault(); seek(target); }
        }}
      >
        <Timeline
          ref={timeline}
          editorData={editableRows}
          effects={{}}
          autoReRender={false}
          disableDrag={disabled || !onRangeChange}
          scale={Math.max(1 / 30, duration) / 10}
          scaleWidth={Math.max(40, (width - 42) / 10)}
          scaleSplitCount={5}
          getScaleRender={(value) => Number(value.toFixed(2))}
          minScaleCount={10}
          maxScaleCount={10}
          rowHeight={64}
          style={{ width: "100%", height: Math.min(442, 58 + rows.length * 64) }}
          getActionRender={(action) => {
            // editorData 保留完整 PreviewClip；使用当前 action 的数据，适配组件内部的异步更新。
            const clip = action as PreviewClip;
            const label = `${clip.label}：${Number(clip.start.toFixed(4))}～${Number(clip.end.toFixed(4))} 秒`;
            // 短区间保持名称与时间各占一行，悬停可读取完整内容。
            return clip.targetId ? <div className={`flex h-full flex-col justify-center overflow-hidden rounded border px-2 text-xs ${selectedId === clip.targetId ? "border-primary bg-primary/20" : "border-border bg-accent"}`} aria-label={label} title={label}>
              <span className="shrink-0 truncate">{clip.label}</span><span className="shrink-0 truncate">{clip.start.toFixed(1)}～{clip.end.toFixed(1)} 秒</span>
            </div> : <ClipThumbnails clip={clip} cache={cache.current} />;
          }}
          onClickAction={(_event, { action }) => { if (rows.some((row) => row.actions.some((clip) => clip.id === action.id && clip.targetId))) onSelect?.(action.id); }}
          onActionMoveStart={({ action }) => { onSelect?.(action.id); seek(time); }}
          onActionResizeStart={({ action }) => { onSelect?.(action.id); seek(time); }}
          onActionMoving={({ start, end }) => start >= 0 && end <= duration}
          onActionResizing={({ start, end }) => start >= 0 && end <= duration && end > start}
          onChange={(updated) => {
            for (const row of updated) for (const action of row.actions) {
              const original = rows.flatMap((item) => item.actions).find((item) => item.id === action.id);
              if (original?.targetId && (Math.abs(original.start - action.start) > 0.000001 || Math.abs(original.end - action.end) > 0.000001))
                onRangeChange?.(original.targetId, action.start, action.end);
            }
            setEditableRows(structuredClone(rows));
            return false;
          }}
          onClickTimeArea={(value) => { seek(value); return false; }}
          onClickRow={(_event, { time: value }) => seek(value)}
          onCursorDragStart={(value) => { dragging.current = true; seek(value); }}
          onCursorDrag={seek}
          onCursorDragEnd={(value) => { dragging.current = false; seek(value); }}
        />
      </div>
    </div>
  );
}
