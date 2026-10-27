/** 视频与效果片段固定显示，SDK 驱动游标，卸载取消缩略图任务。 */
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { Timeline, type TimelineState } from "@xzdarcy/react-timeline-editor";
import { Film, Layers, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { buildPreviewRows, PreviewClip } from "./timeline";
import { objectIcons } from "./EffectAssets";
import type { EffectTarget } from "./effects";
import { loadThumbnails } from "./thumbnails";
import "@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css";
import "./preview-timeline.css";

/** 父组件仅推送播放时间，轨道不启动自身播放计时。 */
export interface PreviewTimelineHandle {
  setTime(time: number): void;
}

/** 轨道消费按当前草稿生成的数据；定位统一交给持有 SDK 的父组件处理。 */
interface Props {
  ref: Ref<PreviewTimelineHandle>;
  rows: ReturnType<typeof buildPreviewRows>;
  disabled: boolean;
  time: number;
  onSeek: (time: number) => void;
  duration?: number;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
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
    <div className="template-timeline-video-clip relative h-full overflow-hidden rounded-md" aria-label={`${clip.label}：${clip.start}～${clip.end} 秒`}>
      <div className="flex h-full" aria-hidden="true">
        {images.map((src, index) => <img key={index} src={src} alt="" draggable={false} className="h-full min-w-0 flex-1 object-cover" />)}
      </div>
      <span className="absolute inset-y-0 left-2 flex items-center gap-1 text-[11px] text-white"><Film className="size-3" aria-hidden="true" />{clip.label}</span>
      {error && <div className="absolute inset-y-0 right-1 flex items-center gap-1 text-[11px] text-white/80">
        <span role="status" className="sr-only" title={error}>{error}</span>
        <Button type="button" size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); setAttempt((value) => value + 1); }} className="h-5 px-1 text-[11px] text-white hover:bg-white/15 hover:text-white">重试缩略图</Button>
      </div>}
    </div>
  );
}

/** 轨道宽度随容器调整；鼠标拖动期间忽略播放器旧时间，键盘支持 0.1 秒定位。 */
export function PreviewTimeline({ ref, rows, disabled, time, onSeek, duration = 30, selectedId, onSelect }: Props) {
  const timeline = useRef<TimelineState>(null);
  const container = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const cache = useRef(new Map<string, string>());
  const [width, setWidth] = useState(400);
  const [editableRows, setEditableRows] = useState(() => structuredClone(rows));
  const startLeft = 20;
  const scale = Math.max(1 / 30, duration) / 5;
  const scaleWidth = Math.max(40, (width - 42) / 5);
  // 时间轴组件会原地修改输入，编辑副本与父组件生成的轨道数据分别持有。
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
  /** 游标拖动与键盘定位限制到当前母版范围。 */
  function seek(value: number) {
    if (disabled) return;
    const position = Math.min(duration, Math.max(0, value));
    timeline.current?.setTime(position);
    onSeek(position);
  }
  return (
    <div className="preview-timeline flex min-w-0 gap-3" role="group" aria-label="视频轨道">
      <div className="template-timeline-labels w-[90px] shrink-0" aria-label="时间轴轨道标签">
        <div className="h-8" aria-hidden="true" />
        {rows.map((row) => {
          const clip = row.actions[0];
          if (row.id === "transitions" && !clip) {
            const Icon = objectIcons.transition;
            return <button key={row.id} type="button" data-object="transition" onClick={() => onSelect?.("transitions")} className="template-timeline-label flex h-[42px] w-full items-center gap-1.5 text-left"><Icon className="size-3.5 shrink-0" aria-hidden="true" /><span className="text-[11px]">镜头转场</span></button>;
          }
          if (!clip) throw new Error("时间轴轨道缺少片段");
          if (clip.effectId === "frame-mask") return <button key={row.id} type="button" data-object="frame-mask" aria-label="编辑上下蒙版" aria-pressed={selectedId === clip.targetId}
            onClick={() => onSelect?.(clip.targetId!)} className="template-timeline-label flex h-[42px] w-full items-center gap-1.5 text-left"><Layers className="size-3.5 shrink-0" aria-hidden="true" /><span className="text-[11px]">上下蒙版</span></button>;
          const Icon = clip.effectId === "video" ? Film : clip.effectId === "remotion" ? Wand2 : objectIcons[clip.effectId as EffectTarget];
          if (!Icon) throw new Error("时间轴轨道类型无效");
          return <div key={row.id} data-object={clip.effectId} className="template-timeline-label flex h-[42px] min-w-0 items-center gap-1.5" title={clip.label}><Icon className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate text-[11px]">{clip.effectId === "video" ? "视频" : clip.effectId === "transition" ? "镜头转场" : clip.label}</span></div>;
        })}
      </div>
      <div
        ref={container}
        className="min-w-0 flex-1 overflow-hidden rounded-md focus-visible:outline-2 focus-visible:outline-ring"
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
          disableDrag
          startLeft={startLeft}
          scale={scale}
          scaleWidth={scaleWidth}
          scaleSplitCount={5}
          getScaleRender={(value) => Number(value.toFixed(2))}
          minScaleCount={5}
          maxScaleCount={5}
          rowHeight={42}
          style={{ width: "100%", height: 32 + rows.length * 42 }}
          getActionRender={(action) => {
            // editorData 保留完整 PreviewClip；使用当前 action 的数据，适配组件内部的异步更新。
            const clip = action as PreviewClip;
            const label = `${clip.label}：${Number(clip.start.toFixed(4))}～${Number(clip.end.toFixed(4))} 秒`;
            // 片段展示效果名称，悬停时提供对象名称与完整时间范围。
            return clip.targetId ? <div data-object={clip.effectId} className={`template-timeline-clip flex h-full items-center overflow-hidden rounded-md px-2 text-[11px] ${selectedId === clip.targetId ? "is-selected" : ""}`} aria-label={label} title={label}>
              <span className="truncate">{clip.effectName ?? clip.label}</span>
            </div> : <ClipThumbnails clip={clip} cache={cache.current} />;
          }}
          onClickAction={(_event, { action }) => { const target = rows.flatMap((row) => row.actions).find((clip) => clip.id === action.id)?.targetId; if (target) onSelect?.(target); }}
          onChange={() => false}
          onClickTimeArea={() => false}
          onCursorDragStart={(value) => { dragging.current = true; seek(value); }}
          onCursorDrag={seek}
          onCursorDragEnd={(value) => { dragging.current = false; seek(value); }}
        />
      </div>
    </div>
  );
}
