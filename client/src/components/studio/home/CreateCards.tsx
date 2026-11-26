/**
 * 主页「开始创作」的两张入口卡片：新建云端模板、AI 生成字效。
 * 两幅插图都在卡片右侧居中，画面使用同一族石墨色「视频画面」；悬停时有轻微动作。
 * AI 卡片以淡淡的强调色底区分，字效画面悬停时掠过一道光泽。
 */
import type { ReactNode } from "react";
import { Icon } from "../icons";
import { cn } from "@/lib/utils";

interface CreateCardsProps {
  onCreateCloud: () => void;
  onOpenEffects: () => void;
}

/** 两张卡片横排，窄于 1024px 时纵向堆叠，小于 640px 隐藏插图以保留文字空间。 */
export function CreateCards({ onCreateCloud, onOpenEffects }: CreateCardsProps) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <EntryCard icon={<Icon name="cloud-plus" />} iconClassName="text-white" title="新建云端模板" description="团队共享，保存后人人可用" onClick={onCreateCloud} art={<CloudArt />} />
      <EntryCard
        icon={<Icon name="stars" variant="duotone" className="animate-entry-spark" />}
        iconClassName="text-white"
        title="AI 生成字效"
        description="描述想法，对话生成字效"
        onClick={onOpenEffects}
        art={<EffectArt />}
        className="border-brand/20 bg-[linear-gradient(135deg,color-mix(in_oklch,var(--color-brand)_7%,var(--color-surface)),var(--color-surface)_60%)] hover:border-brand/35"
      />
    </div>
  );
}

interface EntryCardProps {
  icon: ReactNode;
  iconClassName?: string;
  title: string;
  description: string;
  art: ReactNode;
  onClick: () => void;
  className?: string;
}

/** 单张入口卡：图标、两行文案与预览横向居中；箭头紧随说明并在悬停时右移。 */
function EntryCard({ icon, iconClassName, title, description, art, onClick, className }: EntryCardProps) {
  return (
    <button
      type="button"
      aria-label={title}
      onClick={onClick}
      className={cn(
        "group relative flex h-[120px] w-full items-center gap-4 overflow-hidden rounded-[18px] border border-line bg-surface p-5 text-left transition-[background-color,border-color] duration-200 hover:border-line-strong hover:bg-raised",
        className,
      )}
    >
      <span className={cn("relative z-10 flex size-10 shrink-0 items-center justify-center rounded-xl bg-entry-icon-bg [&_svg]:size-[21px]", iconClassName)}>{icon}</span>
      <span className="relative z-10 flex min-w-0 flex-1 flex-col justify-center">
        <span className="text-[13px] font-semibold tracking-tight">{title}</span>
        <span className="mt-1 flex items-center gap-1.5 text-[12.5px] text-fg-muted">
          <span className="truncate">{description}</span>
          <Icon name="arrow-right" className="size-3.5 shrink-0 text-fg-subtle transition-[transform,color] duration-200 group-hover:translate-x-1 group-hover:text-brand" />
        </span>
      </span>
      <span aria-hidden="true" className="pointer-events-none relative h-full w-[136px] shrink-0 max-sm:hidden xl:w-40">{art}</span>
    </button>
  );
}

/** 插图里的「视频画面」：同一族石墨色渐变，tone 只微调色相与明暗，两张卡片的画面因此统一。 */
const footage = [
  "radial-gradient(90% 60% at 25% 8%, oklch(0.62 0.008 85 / 0.55), transparent 65%), linear-gradient(160deg, oklch(0.4 0.008 85), oklch(0.19 0.008 85))",
  "radial-gradient(90% 60% at 25% 8%, oklch(0.6 0.008 85 / 0.5), transparent 65%), linear-gradient(160deg, oklch(0.36 0.008 85), oklch(0.17 0.008 85))",
  "radial-gradient(90% 60% at 25% 8%, oklch(0.62 0.008 85 / 0.5), transparent 65%), linear-gradient(160deg, oklch(0.38 0.008 85), oklch(0.18 0.008 85))",
];

/** 悬停动作共用的回弹曲线，带一点过冲。 */
const springy = "transition-transform duration-500 ease-[cubic-bezier(0.34,1.4,0.64,1)]";

/** 一格竖屏画面：胶片颗粒上用白色细线示意标题与字幕，代表一个模板。 */
function Frame({ tone = 0, className }: { tone?: number; className?: string }) {
  return (
    <span className={cn("absolute overflow-hidden rounded-lg shadow-lift ring-1 ring-black/10", className)} style={{ background: footage[tone] }}>
      <span className="grain absolute inset-0 opacity-20 mix-blend-overlay" />
      <span className="absolute left-1/2 top-[20%] h-[3px] w-1/2 -translate-x-1/2 rounded-full bg-white/90" />
      <span className="absolute left-1/2 top-[20%] mt-[6px] h-[2px] w-[30%] -translate-x-1/2 rounded-full bg-white/45" />
      <span className="absolute bottom-[12%] left-1/2 h-[2px] w-[58%] -translate-x-1/2 rounded-full bg-white/35" />
    </span>
  );
}

/** 云端插图：三张竖屏画面扇形展开，悬停时展得更开。 */
function CloudArt() {
  return (
    <span className="absolute inset-0">
      <Frame tone={1} className={cn("bottom-2 right-[96px] h-[54px] w-[38px] origin-bottom -rotate-[11deg] group-hover:-translate-x-1 group-hover:-rotate-[16deg]", springy)} />
      <Frame tone={2} className={cn("bottom-2 right-[34px] h-[54px] w-[38px] origin-bottom rotate-[11deg] group-hover:translate-x-1 group-hover:rotate-[16deg]", springy)} />
      <Frame className={cn("bottom-2 right-[62px] h-[62px] w-[44px] group-hover:-translate-y-1", springy)} />
    </span>
  );
}

/**
 * AI 字效插图：右侧生成的字效画面；悬停时一道光泽从字效上扫过。
 */
function EffectArt() {
  return (
    <span className="absolute inset-0">
      <span className="absolute bottom-2 right-0 flex h-[62px] w-[136px] items-center justify-center overflow-hidden rounded-xl shadow-lift ring-1 ring-black/10" style={{ background: footage[0] }}>
        <span className="grain absolute inset-0 opacity-20 mix-blend-overlay" />
        <span className="relative font-serif text-[19px] font-bold tracking-[0.3em] text-white [text-shadow:0_0_14px_oklch(0.8_0.12_265/0.7)]">电影感</span>
        <span className="absolute inset-y-0 -left-1/2 w-1/3 -skew-x-12 bg-linear-to-r from-transparent via-white/25 to-transparent transition-transform duration-700 ease-out group-hover:translate-x-[420%] group-hover:delay-500" />
        <Icon name="stars" variant="duotone" className="absolute right-1.5 top-1.5 size-3.5 text-white/70" />
      </span>
    </span>
  );
}
