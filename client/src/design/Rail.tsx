/**
 * 左侧导航栏：72px 宽的竖向导航，图标下方始终显示文字，给编辑页留出最多的横向空间。
 * 图标默认线性，选中时换成双色并染上强调色，强调色底在项目之间滑动；
 * 正在编辑的模板有未保存修改时，「模板编辑」图标上显示呼吸圆点。底部是设置入口与客户端版本号。
 */
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { Icon, type IconName } from "./icons";
import { LogoMark } from "./Logo";
import { PulseDot, Tooltip } from "./ui";

export type Page = "home" | "editor" | "effects" | "fonts";

/** 导航项与正式客户端的工作区一一对应。 */
const items: { page: Page; label: string; icon: IconName }[] = [
  { page: "home", label: "主页", icon: "home-smile-angle" },
  { page: "editor", label: "模板编辑", icon: "clapperboard-edit" },
  { page: "effects", label: "AI 字效", icon: "stars" },
  { page: "fonts", label: "字体库", icon: "text-square" },
];

interface RailProps {
  page: Page;
  onNavigate: (page: Page) => void;
  onSettings: () => void;
  /** 当前打开的模板名称；没有打开时为 null。 */
  editing: string | null;
  dirty: boolean;
  version: string;
  /** 开场播放期间隐藏标志，开场结束时由开场里的标志飞到这里交接。 */
  logoHidden?: boolean;
}

/** 导航栏本体；选中项用 aria-current 标记，「模板编辑」的悬停提示补充当前模板与保存状态。 */
export function Rail({ page, onNavigate, onSettings, editing, dirty, version, logoHidden }: RailProps) {
  return (
    <nav aria-label="主导航" className="flex h-dvh w-[72px] shrink-0 flex-col items-center border-r border-line bg-rail pb-3 pt-4">
      <Tooltip label="IntelligentMixVideo" side="right">
        <span data-splash-target className={cn("mb-6 flex size-10 items-center justify-center", logoHidden && "opacity-0")}>
          <LogoMark className="size-8 drop-shadow-[0_2px_6px_color-mix(in_oklch,var(--color-brand-to)_35%,transparent)]" />
        </span>
      </Tooltip>
      <ul className="flex w-full flex-col gap-2 px-2">
        {items.map(({ page: target, label, icon }) => {
          const active = page === target;
          const showDot = target === "editor" && editing !== null && dirty;
          const item = (
            <button
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => onNavigate(target)}
              className="group flex w-full flex-col items-center gap-1 rounded-xl py-1.5 focus-visible:outline-offset-0"
            >
              <span className={cn("relative flex h-8 w-12 items-center justify-center rounded-full transition-colors duration-150", active ? "text-accent" : "text-fg-subtle group-hover:bg-raised group-hover:text-fg")}>
                {active && <motion.span layoutId="rail-pill" transition={{ type: "spring", stiffness: 500, damping: 38 }} className="absolute inset-0 rounded-full bg-accent/12" />}
                <Icon name={icon} variant={active ? "duotone" : "linear"} className="relative size-[21px]" />
                {showDot && <PulseDot className="absolute right-2 top-0.5 text-warning ring-2 ring-rail" />}
              </span>
              <span className={cn("text-[11px] leading-none transition-colors", active ? "font-medium text-fg" : "text-fg-subtle group-hover:text-fg-muted")}>{label}</span>
            </button>
          );
          return (
            <li key={target}>
              {target === "editor" ? (
                <Tooltip side="right" label={editing ? `正在编辑「${editing}」${dirty ? " · 有未保存的修改" : ""}` : "还没有打开模板"}>
                  {item}
                </Tooltip>
              ) : item}
            </li>
          );
        })}
      </ul>
      <div className="mt-auto flex w-full flex-col items-center gap-2 px-2">
        <button type="button" aria-haspopup="dialog" onClick={onSettings} className="group flex w-full flex-col items-center gap-1 rounded-xl py-1.5">
          <span className="flex h-8 w-12 items-center justify-center rounded-full text-fg-subtle transition-colors group-hover:bg-raised group-hover:text-fg">
            <Icon name="settings" className="size-[21px]" />
          </span>
          <span className="text-[11px] leading-none text-fg-subtle group-hover:text-fg-muted">设置</span>
        </button>
        <span className="font-mono text-[10px] text-fg-subtle/70">v{version}</span>
      </div>
    </nav>
  );
}
