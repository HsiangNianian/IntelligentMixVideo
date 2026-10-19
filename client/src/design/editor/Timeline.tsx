/**
 * 编辑页底部时间轴：左列是轨道名，右侧是标尺、五条对象轨道（标题、字幕、气泡、画面特效、滤镜）与视频轨道。
 * 片段按类型使用同族淡色（文字靛蓝、特效紫、滤镜蓝），选中时变为实色；视频轨道显示三段素材的胶片条，
 * 转场作为标签放在素材连接处，点击打开转场设置，没有转场的连接处悬停时显示「+」。
 * 点击或拖动标尺定位播放位置；时间轴按总时长等比铺满宽度。
 */
import { useRef, type PointerEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Icon, type IconName } from "../icons";
import { Tooltip } from "../ui";
import { footage, formatTime, joins, objectMeta, spanOf, totalDuration, transitionAt, videos, type EditorObject, type ObjectKind, type TextObject, type Transitions } from "./model";

type Selection = ObjectKind | "transitions" | null;

interface TimelineProps {
  objects: Record<ObjectKind, EditorObject>;
  transitions: Transitions;
  time: number;
  selected: Selection;
  onSelect: (selection: Selection) => void;
  onSeek: (time: number) => void;
}

const rows: ObjectKind[] = ["title", "subtitle", "bubble", "vfx", "filter"];

/** 各类片段的颜色：平时淡色底加同色文字，选中时实色底加白字。 */
const tones: Record<ObjectKind, { idle: string; active: string }> = {
  title: { idle: "bg-accent/10 text-accent ring-accent/25 hover:bg-accent/16", active: "bg-accent text-accent-ink ring-accent" },
  subtitle: { idle: "bg-accent/10 text-accent ring-accent/25 hover:bg-accent/16", active: "bg-accent text-accent-ink ring-accent" },
  bubble: { idle: "bg-accent/10 text-accent ring-accent/25 hover:bg-accent/16", active: "bg-accent text-accent-ink ring-accent" },
  vfx: { idle: "bg-spectrum-3/10 text-spectrum-3 ring-spectrum-3/25 hover:bg-spectrum-3/16", active: "bg-spectrum-3 text-white ring-spectrum-3" },
  filter: { idle: "bg-spectrum-1/10 text-spectrum-1 ring-spectrum-1/25 hover:bg-spectrum-1/16", active: "bg-spectrum-1 text-white ring-spectrum-1" },
};

/** 秒数换算成时间轴上的百分比位置。 */
const at = (seconds: number) => `${(seconds / totalDuration) * 100}%`;

/** 时间轴本体。 */
export function Timeline({ objects, transitions, time, selected, onSelect, onSeek }: TimelineProps) {
  const scale = useRef<HTMLDivElement>(null);
  /** 按指针横坐标定位；按下后捕获指针，拖动时持续定位。 */
  const seek = (event: PointerEvent<HTMLElement>) => {
    const box = scale.current?.getBoundingClientRect();
    if (!box) return;
    onSeek(Math.min(totalDuration, Math.max(0, ((event.clientX - box.left) / box.width) * totalDuration)));
  };
  return (
    <div className="flex h-[250px] shrink-0 border-t border-line bg-surface">
      <div className="w-[108px] shrink-0 border-r border-line">
        <div className="flex h-8 items-center border-b border-line px-3 text-[11px] text-fg-subtle">轨道</div>
        {rows.map((kind) => (
          <TrackHead key={kind} icon={objectMeta[kind].icon} label={objectMeta[kind].label} active={selected === kind} onClick={() => onSelect(kind)} />
        ))}
        <TrackHead icon="videocamera" label="视频" active={selected === "transitions"} onClick={() => onSelect("transitions")} />
      </div>

      <div className="relative min-w-0 flex-1 overflow-hidden" onClick={() => onSelect(null)}>
        <div
          className="h-8 cursor-ew-resize border-b border-line"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            seek(event);
          }}
          onPointerMove={(event) => event.currentTarget.hasPointerCapture(event.pointerId) && seek(event)}
        >
          <div ref={scale} className="relative mx-4 h-full">
            {Array.from({ length: Math.floor(totalDuration) + 1 }, (_, second) => (
              <span key={second} className="absolute bottom-0 h-full" style={{ left: at(second) }}>
                <span className={cn("absolute bottom-0 w-px bg-line-strong", second % 2 ? "h-1.5" : "h-2.5")} />
                {second % 2 === 0 && <span className="absolute left-1 top-1.5 font-mono text-[10px] text-fg-subtle">{second}s</span>}
              </span>
            ))}
          </div>
        </div>

        {rows.map((kind) => {
          const object = objects[kind];
          const [start, end] = spanOf(object.timing);
          const missing = (kind === "vfx" || kind === "filter") && !object.effect;
          const label = "text" in object ? (object as TextObject).text : object.effect;
          return (
            <Lane key={kind}>
              {missing ? (
                <span className="absolute inset-y-[6px] left-0 right-0 flex items-center rounded-md border border-dashed border-line-strong px-2 text-[11px] text-fg-subtle">从左侧素材添加{objectMeta[kind].label}</span>
              ) : (
                <Tooltip label={`${objectMeta[kind].label} · ${start.toFixed(1)}~${end.toFixed(1)} 秒`}>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(kind);
                    }}
                    className={cn("absolute inset-y-[5px] flex min-w-6 items-center gap-1.5 overflow-hidden rounded-md px-2 text-[11px] font-medium ring-1 ring-inset transition-colors", selected === kind ? `${tones[kind].active} shadow-lift` : tones[kind].idle)}
                    style={{ left: at(start), width: at(end - start) }}
                  >
                    <Icon name={objectMeta[kind].icon} className="size-3 shrink-0" />
                    <span className="truncate">{label}</span>
                  </button>
                </Tooltip>
              )}
            </Lane>
          );
        })}

        <Lane className="h-[46px]">
          {videos.map((video, index) => {
            const begin = videos.slice(0, index).reduce((sum, item) => sum + item.duration, 0);
            return (
              <span key={video.label} className="absolute inset-y-[5px] overflow-hidden rounded-md ring-1 ring-inset ring-black/10" style={{ left: `calc(${at(begin)} + 1px)`, width: `calc(${at(video.duration)} - 2px)`, background: footage[video.tone] }}>
                <span className="absolute inset-0 bg-[repeating-linear-gradient(90deg,transparent_0_33px,oklch(0_0_0/0.28)_33px_34px)]" />
                {/* 名称与时长放在左侧，前面有连接处的片段向右让出转场标签的位置。 */}
                <span className={cn("absolute top-1/2 flex -translate-y-1/2 items-center gap-1 whitespace-nowrap rounded bg-black/45 px-1.5 py-0.5 text-[10px] font-medium text-white backdrop-blur-sm", index ? "left-11" : "left-1.5")}>
                  {video.label}
                  <span className="font-mono font-normal text-white/70">{video.duration.toFixed(1)}s</span>
                </span>
              </span>
            );
          })}
          {joins.map((join, index) => {
            const name = transitionAt(transitions, index);
            const active = selected === "transitions";
            return (
              <Tooltip key={join} label={name ? `转场：${name}（${transitions.duration} 秒）` : "添加转场"}>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect("transitions");
                  }}
                  className={cn(
                    "group/join absolute top-1/2 z-10 flex h-6 -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded-full text-[10.5px] font-medium shadow-lift ring-1 transition-colors [&_svg]:size-3",
                    name ? "px-2" : "w-6 justify-center opacity-0 group-hover/lane:opacity-100",
                    active ? "bg-accent text-accent-ink ring-accent" : "bg-surface text-fg ring-line-strong hover:ring-accent/60",
                  )}
                  style={{ left: at(join) }}
                >
                  <Icon name={name ? "transfer-horizontal" : "add-circle"} />
                  {name}
                </button>
              </Tooltip>
            );
          })}
        </Lane>

        {/* 播放头：标尺上的指示块加贯穿全部轨道的竖线。 */}
        <div className="pointer-events-none absolute inset-y-0 left-4 right-4">
          <div className="absolute inset-y-0 w-px bg-accent" style={{ left: at(time) }}>
            <span className="absolute -left-[5.5px] top-0 h-3.5 w-3 bg-accent [clip-path:polygon(0_0,100%_0,100%_60%,50%_100%,0_60%)]" />
            <span className="absolute left-2 top-1 whitespace-nowrap rounded bg-accent px-1 font-mono text-[9.5px] leading-[14px] text-accent-ink">{formatTime(time)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 轨道名：图标加名称，对应对象被选中时高亮。 */
function TrackHead({ icon, label, active, onClick }: { icon: IconName; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn("flex h-[34px] w-full items-center gap-2 px-3 text-left text-xs transition-colors [&_svg]:size-3.5 [&:last-child]:h-[46px]", active ? "bg-raised font-medium text-fg" : "text-fg-muted hover:bg-raised/60 hover:text-fg")}
    >
      <Icon name={icon} />
      {label}
    </button>
  );
}

/** 一条轨道：片段按百分比定位在左右各留 16px 的区域里。 */
function Lane({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("group/lane relative h-[34px] border-b border-line/60", className)}>
      <div className="absolute inset-y-0 left-4 right-4">{children}</div>
    </div>
  );
}
