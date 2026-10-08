/** ObsidianUI sonner 基础组件（基于 shadcn/ui）：全局轻提示容器，颜色取自 popover 主题令牌。
 * 与上游不同：不依赖 next-themes，提示背景、文字和边框直接引用当前主题变量，明暗切换自动生效。
 * Copyright (c) 2026 ObsidianUI、(c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import type { CSSProperties } from "react";
import { CircleCheckIcon, InfoIcon, Loader2Icon, OctagonXIcon, TriangleAlertIcon } from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

/** Toaster 只需在应用根部挂载一次；业务代码通过 sonner 的 toast() 发送提示。 */
function Toaster({ ...props }: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as CSSProperties
      }
      {...props}
    />
  );
}

export { Toaster };
