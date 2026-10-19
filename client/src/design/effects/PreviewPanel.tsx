/**
 * AI 字效的预览区：点阵画布上居中播放器，背景默认是深色棋盘格（表示透明，字效会叠在视频上），可换成浅色或背景视频；
 * 下方是播放条（播放 / 暂停、进度、循环、预览背景）；还没有版本时只显示画布设置。查看历史版本时顶部提示只读并可返回最新。
 * 也导出按参数与时间绘制字效的 EffectFrame，对话里的版本缩略图共用它。正式接入时画面换成隔离的 Remotion 播放器。
 */
import { useState, type CSSProperties } from "react";
import { Popover } from "radix-ui";
import { cn } from "@/lib/utils";
import { Slider } from "../controls";
import { Icon } from "../icons";
import { Button, IconButton, Tooltip } from "../ui";
import { footage } from "../editor/model";
import type { Composition, Params, Version } from "./model";

/**
 * 按参数绘制字效的一帧：文字按 enter 秒淡入，下划线在 0.3 秒后用 1.1 秒从中间展开，光晕与文字同色。
 * 字号与位置按画布宽度换算（容器查询单位），所以缩略图与播放器画面一致。
 */
export function EffectFrame({ params, time, width, className, style }: { params: Params; time: number; width: number; className?: string; style?: CSSProperties }) {
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  const opacity = clamp(time / params.enter);
  const line = clamp((time - 0.3) / 1.1);
  const unit = 100 / width;
  return (
    <div className={cn("absolute inset-0 [container-type:size]", className)} style={style}>
      <div
        className="absolute left-1/2 flex flex-col items-center whitespace-nowrap leading-tight"
        style={{
          top: `${params.y}%`,
          opacity,
          color: params.color,
          fontSize: `${params.fontSize * unit}cqw`,
          fontWeight: Number(params.fontWeight),
          fontFamily: params.fontFamily.includes("宋") ? "var(--font-serif)" : undefined,
          letterSpacing: "0.06em",
          textShadow: params.glow ? `0 0 ${params.glow * 22 * unit}cqw color-mix(in oklch, ${params.color} 70%, transparent)` : undefined,
          transform: `translate(-50%, calc(-50% + ${(1 - opacity) * 2}cqw))`,
        }}
      >
        {params.text}
        {params.underline > 0 && (
          <span className="mt-[0.18em] block w-full rounded-full" style={{ height: `${params.underline * unit}cqw`, background: params.color, transform: `scaleX(${line})`, boxShadow: params.glow ? `0 0 ${params.glow * 14 * unit}cqw color-mix(in oklch, ${params.color} 70%, transparent)` : undefined }} />
        )}
      </div>
    </div>
  );
}

interface PreviewPanelProps {
  version: Version | null;
  params: Params | null;
  composition: Composition;
  history: boolean;
  time: number;
  playing: boolean;
  loop: boolean;
  onTogglePlay: () => void;
  onSeek: (time: number) => void;
  onToggleLoop: () => void;
  onBackToLatest: () => void;
}

/** 预览背景：深色或浅色棋盘格（表示透明），或一段背景视频（url）。 */
type Backdrop = "dark" | "light" | string;

/** 预览区本体。画框放在尺寸容器里，宽度取「容器宽」与「容器高 × 画布宽高比」中较小者，横竖画布都能完整显示。 */
export function PreviewPanel({ version, params, composition, history, time, playing, loop, onTogglePlay, onSeek, onToggleLoop, onBackToLatest }: PreviewPanelProps) {
  const [backdrop, setBackdrop] = useState<Backdrop>("dark");
  const frame: CSSProperties = { aspectRatio: `${composition.width} / ${composition.height}`, width: `min(100cqw, calc(100cqh * ${composition.width / composition.height}))` };
  return (
    <div className="flex min-w-0 flex-1 flex-col border-l border-line">
      <div className="relative min-h-0 flex-1 overflow-hidden bg-canvas bg-[radial-gradient(var(--color-line-strong)_1px,transparent_1px)] bg-size-[18px_18px]">
        {history && version && (
          <div className="absolute inset-x-4 top-3 z-10 flex items-center gap-2 rounded-xl border border-warning/30 bg-surface/95 px-3 py-2 text-xs shadow-lift backdrop-blur">
            <Icon name="history" className="size-4 shrink-0 text-warning" />
            <span className="min-w-0 flex-1 truncate text-fg-muted">正在查看 <b className="font-medium text-fg">V{version.number}</b> · 只读</span>
            <Button size="sm" variant="secondary" className="h-7" onClick={onBackToLatest}>返回最新</Button>
          </div>
        )}
        <div className="absolute inset-6 flex items-center justify-center [container-type:size]">
          {version && params ? (
            <div className="relative overflow-hidden rounded-[10px] shadow-pop ring-1 ring-black/10" style={{ ...frame, background: backdrop === "dark" || backdrop === "light" ? checkers[backdrop] : undefined }}>
              {backdrop !== "dark" && backdrop !== "light" && <div className="absolute inset-0" style={{ background: footage[1] }}><div className="grain absolute inset-0 opacity-20 mix-blend-overlay" /></div>}
              <EffectFrame params={params} time={time} width={composition.width} />
              <span className="absolute left-2.5 top-2.5 rounded-md bg-black/50 px-1.5 py-0.5 font-mono text-[10.5px] text-white backdrop-blur-md">V{version.number}</span>
            </div>
          ) : (
            // 还没有版本：按当前画布比例画一个虚线空画框，修改画布设置时画框跟着变形。
            <div className="flex flex-col items-center justify-center rounded-[10px] border-[1.5px] border-dashed border-line-strong bg-surface/60 px-4 text-center" style={frame}>
              <Icon name="stars" variant="duotone" className="size-6 text-accent/70" />
              <p className="mt-3 text-[13px] font-medium">字效会在这里出现</p>
              <p className="mt-1 max-w-[200px] text-[11.5px] leading-relaxed text-fg-subtle">生成后可以直接播放、调参，并保存到资产</p>
            </div>
          )}
        </div>
      </div>

      {!version ? (
        <div className="flex h-12 shrink-0 items-center justify-center border-t border-line bg-surface font-mono text-[11px] text-fg-subtle">
          {composition.width} × {composition.height} · {composition.fps} fps · {composition.seconds} 秒
        </div>
      ) : (
        <div className="flex h-12 shrink-0 items-center gap-2 border-t border-line bg-surface px-3">
          <Tooltip label={playing ? "暂停" : "播放"}>
            <button type="button" aria-label={playing ? "暂停" : "播放"} onClick={onTogglePlay} className="flex size-8 shrink-0 items-center justify-center rounded-full bg-fg text-canvas transition-transform hover:scale-105 active:scale-95 [&_svg]:size-4">
              <Icon name={playing ? "pause" : "play"} variant="duotone" />
            </button>
          </Tooltip>
          <Slider label="播放进度" value={time} min={0} max={composition.seconds} step={0.01} onChange={onSeek} />
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-fg-muted">{time.toFixed(1)} / {composition.seconds.toFixed(1)}s</span>
          <IconButton label={loop ? "循环播放：开" : "循环播放：关"} onClick={onToggleLoop} className={cn(loop && "bg-accent/12 text-accent hover:bg-accent/15 hover:text-accent")}>
            <Icon name="repeat" />
          </IconButton>
          <BackdropPicker value={backdrop} onChange={setBackdrop} />
        </div>
      )}
    </div>
  );
}

/** 两种棋盘格。 */
const checkers = {
  dark: "repeating-conic-gradient(oklch(0.24 0.004 265) 0 25%, oklch(0.2 0.004 265) 0 50%) 0 0 / 16px 16px",
  light: "repeating-conic-gradient(oklch(0.93 0.003 265) 0 25%, oklch(0.98 0.002 265) 0 50%) 0 0 / 16px 16px",
};

/** 预览背景：深色 / 浅色棋盘格，或粘贴视频直链叠在字效下方（只用于预览，不发给模型）。 */
function BackdropPicker({ value, onChange }: { value: Backdrop; onChange: (value: Backdrop) => void }) {
  const video = value !== "dark" && value !== "light";
  const [draft, setDraft] = useState(video ? value : "");
  const valid = /^https?:\/\/[^\s@]+$/.test(draft.trim());
  const swatch = (kind: "dark" | "light", label: string) => (
    <button type="button" onClick={() => onChange(kind)} className={cn("flex flex-1 flex-col items-center gap-1.5 rounded-xl border p-2 text-[11.5px] transition-colors", value === kind ? "border-accent/60 bg-accent/[0.05] text-fg" : "border-line text-fg-muted hover:border-line-strong")}>
      <span className="h-9 w-full rounded-md ring-1 ring-line" style={{ background: checkers[kind].replace("16px 16px", "8px 8px") }} />
      {label}
    </button>
  );
  return (
    <Popover.Root>
      <Tooltip label="预览背景">
        <Popover.Trigger aria-label="预览背景" className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-raised hover:text-fg data-[state=open]:bg-raised">
          <span className="size-4 rounded-[4px] ring-1 ring-line-strong" style={{ background: video ? footage[1] : checkers[value as "dark" | "light"].replace("16px 16px", "6px 6px") }} />
        </Popover.Trigger>
      </Tooltip>
      <Popover.Portal>
        <Popover.Content side="top" align="end" sideOffset={8} className="z-[70] w-80 space-y-3 rounded-2xl border border-line-strong bg-overlay p-4 shadow-pop data-[state=open]:animate-pop-in">
          <p className="text-[13px] font-medium">预览背景</p>
          <div className="flex gap-2">
            {swatch("dark", "深色透明")}
            {swatch("light", "浅色透明")}
          </div>
          <div className="space-y-1.5">
            <p className="text-[11.5px] leading-relaxed text-fg-subtle">或叠在一段视频上看效果（只用于预览，不会发给模型）</p>
            <div className="flex gap-2">
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="粘贴 http(s) 视频直链"
                className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-sunken px-2.5 text-xs text-fg outline-none placeholder:text-fg-subtle focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15"
              />
              <Button size="sm" variant={video ? "secondary" : "primary"} disabled={!valid} onClick={() => onChange(draft.trim())}>{video ? "更换" : "加载"}</Button>
            </div>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
