/** 侧栏主题切换：沿用 imv.theme 保存选择，首帧恢复由 index.html 完成。 */
import { useState } from "react";
import { Moon, Sun } from "lucide-react";

/** 按钮统一显示「主题」，图标表示当前模式；存储不可用时仍可在当前窗口切换。 */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const label = dark ? "切换到亮色主题" : "切换到暗色主题";
  const Icon = dark ? Moon : Sun;

  function toggle() {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("imv.theme", next ? "dark" : "light"); } catch { /* 当前窗口仍使用所选主题。 */ }
    setDark(next);
  }

  return <button type="button" aria-label="主题" aria-pressed={dark} title={label} onClick={toggle} className="group flex w-full flex-col items-center gap-1 rounded-xl py-1.5">
    <span className="flex h-8 w-12 items-center justify-center rounded-full text-fg-subtle transition-colors group-hover:bg-raised group-hover:text-fg">
      <Icon className="size-[21px]" strokeWidth={1.75} aria-hidden="true" />
    </span>
    <span className="text-[11px] leading-none text-fg-subtle group-hover:text-fg-muted">主题</span>
  </button>;
}
