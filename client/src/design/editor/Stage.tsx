/**
 * 编辑页中间的预览区：点阵画布上居中一帧竖屏画面，下方是播放条。
 * 原型用 CSS 模拟 SDK 预览：三段素材按时间切换（有转场时在连接处交叉淡化）、滤镜与画面特效按区间叠加、
 * 标题 / 气泡 / 字幕按位置、字号、花字与关键词样式绘制。点击画面里的文字即选中，选中对象显示选框与控制点；
 * 点空白处取消选择。正式接入时画面换成 SDK iframe，选框按 SDK 返回的对象区域绘制。
 */
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Icon } from "../icons";
import { Button, IconButton, Tooltip } from "../ui";
import {
  canvas, flowerStyle, footage, formatTime, joins, objectMeta, spanOf, totalDuration, transitionAt, videos,
  type EditorObject, type Keyword, type ObjectKind, type TextKind, type TextObject, type Transitions,
} from "./model";

interface StageProps {
  objects: Record<ObjectKind, EditorObject>;
  transitions: Transitions;
  time: number;
  playing: boolean;
  selected: ObjectKind | "transitions" | null;
  onSelect: (selection: ObjectKind | null) => void;
  onTogglePlay: () => void;
  onRestart: () => void;
  onReplaceVideos: () => void;
}

/** 预览区本体。 */
export function Stage({ objects, transitions, time, playing, selected, onSelect, onTogglePlay, onRestart, onReplaceVideos }: StageProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-canvas bg-[radial-gradient(var(--color-line-strong)_1px,transparent_1px)] bg-size-[18px_18px] px-6 py-5"
        onClick={() => onSelect(null)}
      >
        <Frame objects={objects} transitions={transitions} time={time} selected={selected} onSelect={onSelect} />
      </div>
      <div className="flex h-12 shrink-0 items-center gap-2 border-t border-line bg-surface px-3">
        <IconButton label="从头播放" onClick={onRestart}>
          <Icon name="skip-previous" />
        </IconButton>
        <Tooltip label={playing ? "暂停（空格）" : "播放（空格）"}>
          <button type="button" aria-label={playing ? "暂停" : "播放"} onClick={onTogglePlay} className="flex size-8 items-center justify-center rounded-full bg-fg text-canvas transition-transform hover:scale-105 active:scale-95 [&_svg]:size-4">
            <Icon name={playing ? "pause" : "play"} variant="duotone" />
          </button>
        </Tooltip>
        <span className="ml-1 font-mono text-xs tabular-nums text-fg">
          {formatTime(time)}
          <span className="text-fg-subtle"> / {formatTime(totalDuration)}</span>
        </span>
        <span className="ml-auto rounded-md bg-raised px-2 py-1 font-mono text-[11px] text-fg-muted">{canvas.width} × {canvas.height}</span>
        <Button size="sm" variant="ghost" onClick={onReplaceVideos}>
          <Icon name="video-frame-replace" />
          预览视频
        </Button>
      </div>
    </div>
  );
}

/** 滤镜名称对应的画面处理；没有登记的滤镜用通用的暖色调。 */
const filterLooks: Record<string, string> = {
  暖阳: "sepia(0.25) saturate(1.25)",
  黑白: "grayscale(1)",
  胶片: "contrast(1.1) saturate(0.85) sepia(0.2)",
  冷调: "hue-rotate(-20deg) saturate(1.2)",
  复古: "sepia(0.5) hue-rotate(-8deg)",
};

/** 一帧画面：素材、滤镜、画面特效与三个文字对象叠放；容器查询单位让字号按画布宽度换算。 */
function Frame({ objects, transitions, time, selected, onSelect }: Pick<StageProps, "objects" | "transitions" | "time" | "selected" | "onSelect">) {
  const active = (object: EditorObject) => {
    const [start, end] = spanOf(object.timing);
    return object.kind === "subtitle" || (time >= start && time < end);
  };
  const filter = objects.filter;
  const vfx = objects.vfx;
  return (
    <div className="relative aspect-[9/16] h-full max-w-full overflow-hidden rounded-[10px] bg-black shadow-pop ring-1 ring-black/10 [container-type:size]" onClick={(event) => event.stopPropagation()}>
      <Footage transitions={transitions} time={time} />
      {filter.effect && active(filter) && <div className="pointer-events-none absolute inset-0" style={{ backdropFilter: filterLooks[filter.effect] ?? "sepia(0.2) saturate(1.2)" }} />}
      {vfx.effect && active(vfx) && <Sparkles />}
      <div className="absolute inset-0" onClick={() => onSelect(null)} />
      {(["title", "bubble", "subtitle"] as const).map((kind) => {
        const object = objects[kind] as TextObject;
        if (!active(object)) return null;
        return (
          <TextLayer key={kind} object={object} selected={selected === kind} onSelect={() => onSelect(kind)}>
            {kind === "bubble" ? (
              <span className="relative block rounded-[0.9em] bg-[oklch(0.62_0.2_25)] px-[0.7em] py-[0.35em] font-bold text-white shadow-lift">
                {object.text}
                <span className="absolute -bottom-[0.25em] left-[0.9em] size-[0.6em] rotate-45 rounded-[0.1em] bg-[oklch(0.62_0.2_25)]" />
              </span>
            ) : (
              <span className="block" style={kind === "title" ? flowerStyle(object.effect) : flowerStyle(null)}>
                {highlight(object, kind)}
              </span>
            )}
          </TextLayer>
        );
      })}
    </div>
  );
}

/**
 * 三段素材按时间叠放：后一段在上，转场窗口内由透明渐变到不透明，形成交叉淡化；
 * 没有转场的连接处直接切换。每段画面随播放轻微推近，模拟镜头运动。
 */
function Footage({ transitions, time }: { transitions: Transitions; time: number }) {
  let start = 0;
  return (
    <>
      {videos.map((video, index) => {
        const begin = start;
        start += video.duration;
        const fade = index > 0 && transitionAt(transitions, index - 1) ? transitions.duration : 0;
        const tail = index < joins.length && transitionAt(transitions, index) ? transitions.duration / 2 : 0;
        let opacity = 0;
        if (time >= begin - fade / 2 && time < begin + video.duration + tail) opacity = fade && time < begin + fade / 2 ? (time - (begin - fade / 2)) / fade : 1;
        const progress = Math.min(1, Math.max(0, (time - begin) / video.duration));
        return (
          <div key={video.label} className="absolute inset-0" style={{ opacity, background: footage[video.tone], transform: `scale(${1.02 + progress * 0.05})` }}>
            <div className="grain absolute inset-0 opacity-20 mix-blend-overlay" />
          </div>
        );
      })}
    </>
  );
}

/** 画面特效示意：散落的闪烁光点。 */
function Sparkles() {
  const spots = [[18, 22, 1.2], [72, 16, 0.8], [40, 48, 0.6], [82, 58, 1], [24, 70, 0.9], [60, 78, 0.7], [50, 30, 0.5]];
  return (
    <div className="pointer-events-none absolute inset-0">
      {spots.map(([left, top, scale], index) => (
        <span key={index} className="absolute size-[9cqw] animate-twinkle rounded-full bg-[radial-gradient(circle,#fff_0%,oklch(0.9_0.1_80/0.7)_25%,transparent_65%)]" style={{ left: `${left}%`, top: `${top}%`, transform: `scale(${scale})`, animationDelay: `${index * 0.33}s` }} />
      ))}
    </div>
  );
}

/** 文字对象：按 x / y 百分比定位、按画布宽度换算字号；悬停显示虚线框，选中显示选框与对象名。 */
function TextLayer({ object, selected, onSelect, children }: { object: TextObject; selected: boolean; onSelect: () => void; children: ReactNode }) {
  const style: CSSProperties = { left: `${object.x}%`, top: `${object.y}%`, fontSize: `${(object.size / canvas.width) * 100}cqw`, fontFamily: object.font.includes("宋") ? "var(--font-serif)" : undefined };
  return (
    <button
      type="button"
      aria-label={`选择${objectMeta[object.kind].label}`}
      aria-pressed={selected}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      className="group/text absolute max-w-[92%] -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-center leading-tight outline-none"
      style={style}
    >
      {children}
      {selected ? <SelectionFrame label={objectMeta[object.kind].label} /> : <span className="pointer-events-none absolute -inset-[5px] rounded-[3px] border border-dashed border-white/0 transition-colors group-hover/text:border-white/70" />}
    </button>
  );
}

/** 选框：强调色描边、四角白色控制点、左上角的对象名标签。 */
function SelectionFrame({ label }: { label: string }) {
  const corners = ["-left-[4px] -top-[4px]", "-right-[4px] -top-[4px]", "-bottom-[4px] -left-[4px]", "-bottom-[4px] -right-[4px]"];
  return (
    <span aria-hidden="true" className="pointer-events-none absolute -inset-[6px] rounded-[3px] border-[1.5px] border-accent">
      {corners.map((corner) => <span key={corner} className={cn("absolute size-[7px] rounded-[2px] border-[1.5px] border-accent bg-white", corner)} />)}
      <span className="absolute -top-[21px] -left-[1.5px] whitespace-nowrap rounded-[4px] bg-accent px-1.5 py-[3px] font-sans text-[10px] font-medium leading-none text-accent-ink [text-shadow:none]">{label}</span>
    </span>
  );
}

/** 关键词样式：粗体、斜体、下划线、删除线、颜色与字号，只作用于第一次出现的位置。 */
function keywordStyle(keyword: Keyword): CSSProperties {
  const decoration = [keyword.underline && "underline", keyword.strike && "line-through"].filter(Boolean).join(" ");
  return {
    fontWeight: keyword.bold ? 900 : undefined,
    fontStyle: keyword.italic ? "italic" : undefined,
    textDecoration: decoration || undefined,
    color: keyword.color ?? undefined,
    WebkitTextFillColor: keyword.color ?? undefined,
    fontSize: keyword.size ? `${(keyword.size / canvas.width) * 100}cqw` : undefined,
  };
}

/** 把关键词包成单独的样式片段；字幕在预览里取示例文字的前两个字作关键词。 */
function highlight(object: TextObject, kind: TextKind): ReactNode {
  const word = kind === "subtitle" ? object.text.slice(0, 2) : object.keyword.text;
  const at = word ? object.text.indexOf(word) : -1;
  if (at < 0) return object.text;
  return (
    <>
      {object.text.slice(0, at)}
      <span style={keywordStyle(object.keyword)}>{word}</span>
      {object.text.slice(at + word.length)}
    </>
  );
}
