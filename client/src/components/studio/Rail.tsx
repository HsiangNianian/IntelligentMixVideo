/** 纸面工作室侧栏：宽屏显示图标与文字，窄屏收紧；保留草稿提示、主题、设置及开场标志锚点。 */
import { cn } from "@/lib/utils";
import { Icon, type IconName } from "./icons";
import { LogoMark } from "./Logo";
import { PulseDot, Tooltip } from "./ui";
import { ThemeToggle } from "@/components/ThemeToggle";

export type Page = "home" | "editor" | "effects" | "fonts" | "playground";

/** 导航项与正式客户端的工作区一一对应。 */
const items: { page: Page; label: string; icon: IconName }[] = [
  { page: "home", label: "主页", icon: "home-smile-angle" },
  { page: "editor", label: "模板编辑", icon: "clapperboard-edit" },
  { page: "effects", label: "AI 字效", icon: "stars" },
  { page: "fonts", label: "字体库", icon: "text-square" },
  { page: "playground", label: "操练场", icon: "layers-minimalistic" },
];

interface RailProps {
  page: Page;
  onNavigate: (page: Page) => void;
  onSettings: () => void;
  editing: string | null;
  dirty: boolean;
  version: string;
  /** 开场播放期间隐藏标志，结束后由开场标志飞到此锚点。 */
  logoHidden?: boolean;
}

/** aria-current 标记当前位置；只隐藏未访问的工作区，不在导航中修改业务状态。 */
export function Rail({ page, onNavigate, onSettings, editing, dirty, version, logoHidden }: RailProps) {
  return (
    <nav aria-label="主导航" className="paper-surface flex h-dvh w-[76px] shrink-0 flex-col border-r border-line bg-rail px-2 py-5 xl:w-52 xl:px-5">
      <div className="mb-8 flex h-16 items-start justify-center gap-2 border-b border-line xl:justify-start">
        <span data-splash-target className={cn("flex size-9 shrink-0 items-center justify-center", logoHidden && "opacity-0")}>
          <LogoMark className="studio-logo size-9 text-fg" />
        </span>
        <span aria-label="IMV" className="sketch-text hidden text-[28px] leading-9 xl:inline-block">IMV</span>
      </div>
      <ul className="flex flex-col gap-3">
        {items.map(({ page: target, label, icon }) => {
          const active = page === target;
          const item = (
            <button type="button" aria-label={label} aria-current={active ? "page" : undefined} onClick={() => onNavigate(target)}
              className={cn("group relative flex min-h-12 w-full flex-col items-center justify-center gap-1 border-l-2 py-2 transition-colors xl:flex-row xl:justify-start xl:gap-3 xl:px-3", active ? "border-fg bg-fg/5 text-fg" : "border-transparent text-fg-muted hover:bg-fg/5 hover:text-fg")}>
              <span className="relative shrink-0">
                <Icon name={icon} className="size-5" />
                {target === "editor" && editing !== null && dirty && <PulseDot className="absolute -right-1 -top-1 text-warning ring-2 ring-rail" />}
              </span>
              <span className="font-serif text-[10px] xl:text-[16px]">{label}</span>
            </button>
          );
          return <li key={target}>{target === "editor" ? <Tooltip side="right" label={editing ? `正在编辑「${editing}」${dirty ? " · 有未保存的修改" : ""}` : "还没有打开模板"}>{item}</Tooltip> : item}</li>;
        })}
      </ul>
      <div className="mt-auto flex flex-col items-center gap-3 border-t border-line pt-4">
        <ThemeToggle />
        <button type="button" aria-haspopup="dialog" onClick={onSettings} className="flex w-full flex-col items-center justify-center gap-1 py-2 text-fg-muted hover:text-fg xl:flex-row xl:justify-start xl:gap-3 xl:px-3">
          <Icon name="settings" className="size-5" /><span className="text-[10px] xl:font-serif xl:text-base">设置</span>
        </button>
        <span className="font-mono text-[10px] text-fg-subtle">v{version}</span>
      </div>
    </nav>
  );
}
