/**
 * 主页「开始创作」的三张入口卡片：新建云端模板、新建本地模板、AI 生成字效。
 * 三幅插图同一构图：右上一枚小标签，右下是主体画面，画面都用同一族靛蓝深色「视频画面」；悬停时标签上浮、画面有轻微动作。
 * 浏览器里本地模板不可用，卡片置灰并说明原因。
 * AI 卡片以淡淡的强调色底区分，插图是提示词标签与它生成的字效画面：悬停时提示词逐字重打、光泽扫过字效。
 */
import type { ReactNode } from "react";
import { Icon } from "../icons";
import { cn } from "@/lib/utils";

interface CreateCardsProps {
  /** false 表示浏览器环境，本地模板不可用。 */
  desktop: boolean;
  onCreateCloud: () => void;
  onCreateLocal: () => void;
  onOpenEffects: () => void;
}

/** 三张卡片横排，窄于 960px 时纵向堆叠。 */
export function CreateCards({ desktop, onCreateCloud, onCreateLocal, onOpenEffects }: CreateCardsProps) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <EntryCard icon={<Icon name="cloud-plus" />} title="新建云端模板" description="团队共享，保存后人人可用" onClick={onCreateCloud} art={<CloudArt />} />
      <EntryCard
        icon={<Icon name="laptop-minimalistic" />}
        title="新建本地模板"
        description={desktop ? "存在这台电脑，无需连接服务" : "需要在桌面客户端中使用"}
        disabled={!desktop}
        badge={desktop ? undefined : "仅桌面端"}
        onClick={onCreateLocal}
        art={<LocalArt />}
      />
      <EntryCard
        icon={<Icon name="stars" variant="duotone" />}
        iconClassName="bg-brand/12 text-brand"
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
  disabled?: boolean;
  badge?: string;
  className?: string;
}

/** 单张入口卡：左侧图标、标题与说明，右侧插图；底部的箭头在悬停时右移提示可点击。 */
function EntryCard({ icon, iconClassName, title, description, art, onClick, disabled, badge, className }: EntryCardProps) {
  return (
    <button
      type="button"
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "group relative flex h-[124px] w-full overflow-hidden lg:h-[148px] rounded-[18px] border border-line bg-surface p-5 text-left transition-[background-color,border-color] duration-200 hover:border-line-strong hover:bg-raised disabled:cursor-not-allowed disabled:hover:border-line disabled:hover:bg-surface",
        className,
      )}
    >
      <span className={cn("relative z-10 flex min-w-0 max-w-[62%] flex-1 flex-col", disabled && "opacity-50")}>
        <span className={cn("flex size-10 items-center justify-center rounded-xl bg-raised text-fg [&_svg]:size-[21px]", iconClassName)}>{icon}</span>
        <span className="mt-auto flex items-center gap-2">
          <span className="text-[15px] font-semibold tracking-tight">{title}</span>
          {badge && <span className="rounded-full border border-line-strong px-1.5 py-px text-[10.5px] text-fg-muted">{badge}</span>}
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[12.5px] text-fg-muted">
          <span className="truncate">{description}</span>
          {!disabled && <Icon name="arrow-right" className="size-3.5 shrink-0 text-fg-subtle transition-[transform,color] duration-200 group-hover:translate-x-1 group-hover:text-brand" />}
        </span>
      </span>
      <span aria-hidden="true" className={cn("pointer-events-none absolute inset-y-0 right-0 w-[46%]", disabled && "opacity-35 grayscale")}>{art}</span>
    </button>
  );
}

/** 插图里的「视频画面」：同一族靛蓝深色渐变，tone 只微调色相与明暗，三张卡片的画面因此统一。 */
const footage = [
  "radial-gradient(90% 60% at 25% 8%, oklch(0.62 0.11 268 / 0.55), transparent 65%), linear-gradient(160deg, oklch(0.4 0.07 266), oklch(0.19 0.035 280))",
  "radial-gradient(90% 60% at 25% 8%, oklch(0.6 0.09 250 / 0.5), transparent 65%), linear-gradient(160deg, oklch(0.36 0.06 252), oklch(0.17 0.03 262))",
  "radial-gradient(90% 60% at 25% 8%, oklch(0.62 0.1 292 / 0.5), transparent 65%), linear-gradient(160deg, oklch(0.38 0.07 290), oklch(0.18 0.035 300))",
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

/**
 * 插图右上角的小标签：白底细边，与 AI 卡片的提示词气泡同一位置，悬停时一起上浮。
 * 窄窗口下卡片变矮变宽，标签改到主体画面左侧并垂直居中，避免被画面挡住。
 */
const chipPlace = "right-5 top-6 max-lg:right-[184px] max-lg:top-[calc(50%-14px)]";

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className={cn(chipPlace, "absolute flex h-7 items-center gap-1.5 rounded-full bg-surface pl-1.5 pr-2.5 text-[11px] text-fg-muted shadow-lift ring-1 ring-line transition-transform duration-300 group-hover:-translate-y-1")}>
      {children}
    </span>
  );
}

/** 团队头像：三枚冷色渐变圆点，示意多人共用。 */
const avatars = [
  "from-[oklch(0.8_0.09_225)] to-[oklch(0.6_0.15_258)]",
  "from-[oklch(0.76_0.11_285)] to-[oklch(0.56_0.18_300)]",
  "from-[oklch(0.84_0.07_195)] to-[oklch(0.62_0.12_232)]",
];

/** 云端插图：右上是团队头像与「团队共享」，下方三张竖屏画面扇形展开，悬停时展得更开。 */
function CloudArt() {
  return (
    <span className="absolute inset-0">
      <Chip>
        <span className="flex -space-x-1.5">
          {avatars.map((fill) => <span key={fill} className={cn("size-4 rounded-full bg-linear-to-br ring-2 ring-surface", fill)} />)}
        </span>
        团队共享
      </Chip>
      <Frame tone={1} className={cn("bottom-4 right-[96px] h-[62px] w-[38px] origin-bottom -rotate-[11deg] group-hover:-translate-x-1 group-hover:-rotate-[16deg]", springy)} />
      <Frame tone={2} className={cn("bottom-4 right-[34px] h-[62px] w-[38px] origin-bottom rotate-[11deg] group-hover:translate-x-1 group-hover:rotate-[16deg]", springy)} />
      <Frame className={cn("bottom-4 right-[62px] h-[72px] w-[44px] group-hover:-translate-y-1", springy)} />
    </span>
  );
}

/** 本地插图：右上是「仅本机」，下方一个桌面小窗口里排着三个模板缩略图，悬停时中间一个浮起。 */
function LocalArt() {
  return (
    <span className="absolute inset-0">
      <Chip>
        <Icon name="laptop-minimalistic" className="ml-1 size-3.5 text-fg" />
        仅本机
      </Chip>
      <span className="absolute bottom-4 right-5 w-[132px] overflow-hidden rounded-[10px] bg-surface shadow-lift ring-1 ring-line">
        <span className="flex h-[17px] items-center gap-1 border-b border-line px-2">
          {[0, 1, 2].map((dot) => <span key={dot} className="size-[5px] rounded-full bg-line-strong" />)}
        </span>
        <span className="flex justify-center gap-2 px-2.5 pb-2.5 pt-2">
          {[1, 0, 2].map((tone, index) => (
            <span key={tone} className={cn("relative h-11 w-[27px]", springy, index === 1 && "group-hover:-translate-y-1")}>
              <Frame tone={tone} className="inset-0 rounded-md shadow-none" />
            </span>
          ))}
        </span>
      </span>
    </span>
  );
}

/**
 * AI 字效插图：右上是与其他卡片同款的白色标签，里面是提示词与闪烁的输入光标；下方是生成的字效画面。
 * 悬停时提示词按字逐步显现、光标同步右移（两者用同样的步数与时长），随后一道光泽从字效上扫过。
 */
function EffectArt() {
  return (
    <span className="absolute inset-0">
      <Chip>
        <Icon name="stars" variant="duotone" className="ml-1 size-3.5 text-brand" />
        <span className="relative mr-1 text-fg">
          <span className="block group-hover:animate-type-text">电影感标题</span>
          <span className="absolute left-full top-1/2 ml-px h-3 w-px -translate-y-1/2 animate-caret bg-brand group-hover:[animation:caret_1s_steps(1)_infinite,type-caret_0.8s_steps(5,end)_both]" />
        </span>
      </Chip>
      <span className="absolute bottom-5 right-5 flex h-[62px] w-[136px] max-lg:bottom-[30px] items-center justify-center overflow-hidden rounded-xl shadow-lift ring-1 ring-black/10" style={{ background: footage[0] }}>
        <span className="grain absolute inset-0 opacity-20 mix-blend-overlay" />
        <span className="relative font-serif text-[19px] font-bold tracking-[0.3em] text-white [text-shadow:0_0_14px_oklch(0.8_0.12_265/0.7)]">电影感</span>
        <span className="absolute inset-y-0 -left-1/2 w-1/3 -skew-x-12 bg-linear-to-r from-transparent via-white/25 to-transparent transition-transform duration-700 ease-out group-hover:translate-x-[420%] group-hover:delay-500" />
        <Icon name="stars" variant="duotone" className="absolute right-1.5 top-1.5 size-3.5 text-white/70" />
      </span>
    </span>
  );
}
