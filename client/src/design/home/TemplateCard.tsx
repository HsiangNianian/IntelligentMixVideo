/**
 * 主页模板卡片：封面模拟模板套用后的一帧竖屏画面，下方是名称、说明与更新时间。
 * 悬停交互参考 bencho（MIT，https://github.com/cabralorenzo/bencho）的三个组件，用 motion 重新实现：
 * - Tilt card：封面在指针下方「下陷」，指针处积聚阴影、对侧边缘受光，离开后弹回；
 * - Action node：打开、复制 ID、删除三个圆形按钮从右上角依次弹出；
 * - Dynamic island：左上角的玻璃胶囊在「图层数 / 编辑中 / 未保存」之间平滑变形。
 * 配置无效的模板不能打开，只保留常驻的删除按钮；系统要求减少动态效果时不倾斜。
 */
import type { CSSProperties, PointerEvent, ReactNode } from "react";
import { AnimatePresence, motion, useMotionTemplate, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { cn } from "@/lib/utils";
import { Icon, type IconName } from "../icons";
import { PulseDot, Skeleton, Tooltip } from "../ui";
import type { TemplateSummary } from "../mock";

/** 由模板 ID 得到稳定哈希：FNV-1a，相近 ID 也会落到明显不同的值上。 */
function hashOf(id: string): number {
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  return hash;
}

/** 封面标题的四种「花字」写法，按哈希挑选，让不同模板一眼可分。 */
function titleStyle(hash: number, hue: number): { className: string; style: CSSProperties } {
  // 八个方向的硬阴影模拟描边，比 -webkit-text-stroke 在各 WebView 里表现一致。
  const stroke = (color: string, width: number) =>
    [[1, 0], [-1, 0], [0, 1], [0, -1], [0.7, 0.7], [-0.7, 0.7], [0.7, -0.7], [-0.7, -0.7]]
      .map(([x, y]) => `${x * width}px ${y * width}px 0 ${color}`)
      .join(", ");
  switch (hash % 4) {
    case 0:
      return { className: "font-black", style: { color: "#ffe14d", textShadow: `${stroke("#1d1400", 1.6)}, 0 3px 0 #1d1400` } };
    case 1:
      return { className: "font-bold", style: { color: "#fff", textShadow: `0 0 10px oklch(0.78 0.18 ${hue}), 0 0 22px oklch(0.7 0.2 ${hue} / 0.7)` } };
    case 2:
      return { className: "font-serif font-bold tracking-[0.12em]", style: { color: "#f6e7c8", textShadow: "0 2px 12px rgb(0 0 0 / 55%)" } };
    default:
      return { className: "font-black italic", style: { color: "#fff", textShadow: `${stroke(`oklch(0.55 0.2 ${hue})`, 1.8)}, 0 4px 10px rgb(0 0 0 / 40%)` } };
  }
}

/** 封面：渐变「画面」+ 胶片颗粒 + 暗角，标题在上、字幕在下，与模板里标题与字幕的位置一致；compact 用于对话框里的小缩略图，只保留标题。 */
export function TemplateCover({ template, compact, className }: { template: TemplateSummary; compact?: boolean; className?: string }) {
  const hash = hashOf(template.template_id);
  const hue = Math.round(hash * 137.508) % 360;
  const title = titleStyle(hash, hue);
  return (
    <span
      aria-hidden="true"
      className={cn("relative flex aspect-[4/5] w-full flex-col items-center overflow-hidden", className)}
      style={{
        background: `radial-gradient(120% 70% at 18% 8%, oklch(0.66 0.13 ${hue}) 0%, transparent 60%), radial-gradient(90% 60% at 92% 96%, oklch(0.56 0.15 ${(hue + 70) % 360}) 0%, transparent 70%), linear-gradient(180deg, oklch(0.42 0.08 ${hue}), oklch(0.24 0.05 ${(hue + 30) % 360}))`,
      }}
    >
      <span className="grain absolute inset-0 opacity-[0.16] mix-blend-overlay" />
      <span className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_55%,oklch(0_0_0/0.4)_100%)]" />
      <span className={cn("relative mt-[17%] line-clamp-2 text-center leading-tight", compact ? "px-1 text-[8px]" : "px-3 text-[clamp(13px,7.5cqw,17px)]", title.className)} style={title.style}>
        {template.title || template.name}
      </span>
      {template.subtitle && !compact && (
        <span className="absolute bottom-[9%] mx-3 line-clamp-1 rounded-[3px] bg-black/55 px-1.5 py-0.5 text-[10.5px] text-white/95">{template.subtitle}</span>
      )}
    </span>
  );
}

/** 配置无效时的封面：警示斜纹，明确告诉用户这张卡片打不开。 */
function InvalidCover() {
  return (
    <span aria-hidden="true" className="flex aspect-[4/5] w-full flex-col items-center justify-center gap-2 bg-[repeating-linear-gradient(135deg,color-mix(in_oklch,var(--color-warning)_10%,transparent)_0_10px,transparent_10px_20px)] text-warning">
      <Icon name="danger-triangle" className="size-6" />
      <span className="text-xs font-medium">无法打开</span>
    </span>
  );
}

/** 更新时间写成人读的相对日期：今天、昨天显示时刻，今年只显示月日，往年加上年份。 */
function formatUpdated(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (date.getTime() >= startOfToday) return `今天 ${time}`;
  if (date.getTime() >= startOfToday - 86_400_000) return `昨天 ${time}`;
  if (date.getFullYear() === now.getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

interface CardProps {
  template: TemplateSummary;
  index: number;
  /** 当前在编辑器中打开；dirty 表示还有未保存的修改。 */
  editing: boolean;
  dirty: boolean;
  onOpen: () => void;
  onDelete: () => void;
  onCopyId: () => void;
}

/** 单张模板卡片；进入时按序号错开淡入，被删除时缩小淡出，其余卡片平滑补位。 */
export function TemplateCard({ template, index, editing, dirty, onOpen, onDelete, onCopyId }: CardProps) {
  const invalid = Boolean(template.validation_error);
  const tilt = useSinkTilt(!invalid);
  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.94 }}
      transition={{ duration: 0.28, delay: Math.min(index, 10) * 0.035, ease: [0.2, 0.8, 0.2, 1] }}
      className="group relative [container-type:inline-size]"
    >
      <button
        type="button"
        disabled={invalid}
        onClick={onOpen}
        aria-label={invalid ? `${template.name}（配置无效，不能打开）` : `打开模板「${template.name}」`}
        className="block w-full rounded-xl text-left focus-visible:outline-offset-4"
      >
        {/* 外框不动，指针位置相对它计算；透视放在外框上，消失点不随封面转动。 */}
        <span className="relative block [perspective:800px]" onPointerMove={tilt.track} onPointerLeave={tilt.release}>
          {editing && <span className="absolute -inset-[5px] rounded-[17px] ring-2 ring-accent/45" />}
          <motion.span
            className={cn("relative block overflow-hidden rounded-xl bg-surface ring-1", invalid ? "ring-warning/25" : "ring-line")}
            style={tilt.surface}
          >
            {invalid ? <InvalidCover /> : <TemplateCover template={template} />}
            {!invalid && (
              <>
                <motion.span aria-hidden="true" className="pointer-events-none absolute inset-0" style={{ backgroundImage: tilt.sheen }} />
                <StatusPill state={editing ? (dirty ? "dirty" : "editing") : "idle"} count={template.track_count} />
              </>
            )}
          </motion.span>
        </span>
        <span className="mt-3 block px-0.5">
          <span className="block truncate text-sm font-medium text-fg">{template.name}</span>
          {invalid ? (
            <span className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-warning">{template.validation_error}</span>
          ) : (
            <span className="mt-1 block truncate text-[12.5px] text-fg-muted">{template.description || "没有说明"}</span>
          )}
          <span className="mt-1.5 flex items-center gap-1.5 text-[11px] text-fg-subtle">
            <span>{formatUpdated(template.updated_at)}</span>
            <span aria-hidden="true">·</span>
            <span className="truncate font-mono">{template.template_id.slice(0, 8)}</span>
          </span>
        </span>
      </button>
      <span className="absolute right-2.5 top-2.5 flex flex-col gap-1.5">
        {!invalid && <ActionButton order={0} icon="arrow-right-up" label="打开编辑" tone="accent" onClick={onOpen} />}
        {!invalid && <ActionButton order={1} icon="copy" label="复制模板 ID" onClick={onCopyId} />}
        <ActionButton order={invalid ? 0 : 2} icon="trash-bin-minimalistic" label="删除模板" tone="danger" onClick={onDelete} pinned={invalid} />
      </span>
    </motion.article>
  );
}

/**
 * 「下陷」倾斜：指针位置（-1~1）经弹簧平滑后，同时驱动转角、后退距离、阴影与两层光影，
 * 四者读同一组数值，不会互相错位。指针靠上时上沿向后倒（rotateX 为正），与常见的「迎向指针」相反。
 */
function useSinkTilt(enabled: boolean) {
  const reduce = useReducedMotion();
  const active = enabled && !reduce;
  const spring = { stiffness: 260, damping: 24, mass: 0.7 };
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const pressed = useMotionValue(0);
  const x = useSpring(pointerX, spring);
  const y = useSpring(pointerY, spring);
  const lit = useSpring(pressed, spring);
  const transform = useMotionTemplate`translateZ(${useTransform(lit, (value) => -14 * value)}px) rotateX(${useTransform(y, (value) => -value * 8)}deg) rotateY(${useTransform(x, (value) => value * 8)}deg)`;
  const boxShadow = useMotionTemplate`0 ${useTransform(lit, (value) => 16 - 8 * value)}px ${useTransform(lit, (value) => 36 - 14 * value)}px -14px oklch(0.2 0.01 265 / ${useTransform(lit, (value) => 0.22 + 0.08 * value)})`;
  const atX = useTransform(x, (value) => ((value + 1) / 2) * 100);
  const atY = useTransform(y, (value) => ((value + 1) / 2) * 100);
  const sheen = useMotionTemplate`radial-gradient(42% 34% at ${atX}% ${atY}%, oklch(0.15 0.02 265 / ${useTransform(lit, (value) => 0.32 * value)}) 0%, transparent 100%), radial-gradient(52% 42% at ${useTransform(atX, (value) => 100 - value)}% ${useTransform(atY, (value) => 100 - value)}%, oklch(1 0 0 / ${useTransform(lit, (value) => 0.22 * value)}) 0%, transparent 100%)`;
  return {
    surface: { transform, boxShadow } as const,
    sheen,
    track: (event: PointerEvent<HTMLElement>) => {
      if (!active) return;
      const rect = event.currentTarget.getBoundingClientRect();
      pointerX.set(Math.min(1, Math.max(-1, ((event.clientX - rect.left) / rect.width) * 2 - 1)));
      pointerY.set(Math.min(1, Math.max(-1, ((event.clientY - rect.top) / rect.height) * 2 - 1)));
      pressed.set(1);
    },
    release: () => {
      pointerX.set(0);
      pointerY.set(0);
      pressed.set(0);
    },
  };
}

/** 封面左上角的玻璃胶囊：平时显示图层数，编辑中显示绿点，有未保存修改时显示呼吸的橙点；内容切换时宽度随之变形。 */
function StatusPill({ state, count }: { state: "idle" | "editing" | "dirty"; count: number }) {
  const content: Record<typeof state, ReactNode> = {
    idle: (
      <>
        <Icon name="layers-minimalistic" className="size-3" />
        <span className="font-mono">{count}</span>
      </>
    ),
    editing: (
      <>
        <span className="size-1.5 rounded-full bg-success" />
        编辑中
      </>
    ),
    dirty: (
      <>
        <PulseDot className="size-1.5 text-warning" />
        未保存
      </>
    ),
  };
  return (
    <motion.span
      layout
      transition={{ type: "spring", stiffness: 520, damping: 36 }}
      style={{ borderRadius: 999 }}
      className="absolute left-2.5 top-2.5 flex h-6 items-center overflow-hidden bg-black/50 px-2 text-[10.5px] font-medium text-white backdrop-blur-md"
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={state}
          initial={{ opacity: 0, y: 8, filter: "blur(3px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -8, filter: "blur(3px)" }}
          transition={{ duration: 0.22 }}
          className="flex items-center gap-1.5 whitespace-nowrap"
        >
          {content[state]}
        </motion.span>
      </AnimatePresence>
    </motion.span>
  );
}

const actionTones = {
  neutral: "hover:bg-raised",
  accent: "hover:bg-accent hover:text-accent-ink",
  danger: "hover:bg-danger hover:text-white",
};

/**
 * 圆形操作按钮：悬停卡片时按 order 依次从右上角弹出（回弹曲线带一点过冲），离开时收回；
 * pinned 时常驻显示（配置无效的卡片只有删除可用）。
 */
function ActionButton({ order, icon, label, tone = "neutral", pinned, onClick }: { order: number; icon: IconName; label: string; tone?: keyof typeof actionTones; pinned?: boolean; onClick: () => void }) {
  const style: CSSProperties = { transitionDelay: `${order * 45}ms` };
  return (
    <Tooltip label={label} side="left">
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        style={style}
        className={cn(
          "flex size-8 items-center justify-center rounded-full bg-surface/95 text-fg shadow-lift ring-1 ring-line backdrop-blur-md transition-[opacity,transform,background-color,color] duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)] [&_svg]:size-4",
          pinned ? "opacity-100" : "translate-x-1 -translate-y-1 scale-50 opacity-0 group-hover:translate-0 group-hover:scale-100 group-hover:opacity-100 group-focus-within:translate-0 group-focus-within:scale-100 group-focus-within:opacity-100",
          actionTones[tone],
        )}
      >
        <Icon name={icon} />
      </button>
    </Tooltip>
  );
}

/** 列表加载中的卡片占位，形状与真实卡片一致，加载完成时不跳动。 */
export function TemplateCardSkeleton() {
  return (
    <div aria-hidden="true">
      <Skeleton className="aspect-[4/5] w-full rounded-xl" />
      <Skeleton className="mt-3.5 h-3.5 w-3/4" />
      <Skeleton className="mt-2 h-3 w-1/2" />
    </div>
  );
}
