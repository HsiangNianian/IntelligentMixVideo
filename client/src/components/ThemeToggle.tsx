/** 明暗主题切换按钮：读取 <html> 当前主题，切换 .dark 类并把选择保存到 localStorage；首帧恢复由 index.html 完成。 */
import { useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Hint } from "@/components/Hint";

/** 与 index.html 首帧脚本共用的存储键；未保存时跟随系统主题。 */
const storageKey = "imv.theme";

/** 侧栏底部的主题按钮；collapsed 或窄屏时只显示图标，并通过悬停提示说明切换方向。 */
export function ThemeToggle({ collapsed, className }: { collapsed: boolean; className?: string }) {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const label = dark ? "切换到浅色主题" : "切换到深色主题";

  /** 切换主题；存储不可用（隐私模式等）时只影响当前页面。 */
  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem(storageKey, next ? "dark" : "light"); } catch { /* 存储不可用时不持久化 */ }
    setDark(next);
  }

  const Icon = dark ? Sun : Moon;
  return (
    <Hint label={label} side="right" visibleBelow={collapsed ? undefined : "md"}><Button type="button" variant="ghost" aria-label={label} onClick={toggle} className={className}>
      <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden="true" />
      <span className={cn("hidden", !collapsed && "md:inline")}>{dark ? "浅色主题" : "深色主题"}</span>
    </Button></Hint>
  );
}
