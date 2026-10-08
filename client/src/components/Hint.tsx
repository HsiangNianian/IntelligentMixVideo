/** 图标按钮的悬停提示：把 Tooltip 的触发器、内容组合成一层，替代浏览器原生 title 提示。 */
import type { ComponentProps, ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * children 必须是单个可聚焦元素（通常是 Button），其无障碍名称仍由自身 aria-label 提供；
 * className 作用于提示气泡，例如 md:hidden 只在文字被隐藏的窄屏或收起状态显示。
 */
export function Hint({ label, side = "top", className, children }: {
  label: string;
  side?: ComponentProps<typeof TooltipContent>["side"];
  className?: string;
  children: ReactElement;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className={className}>{label}</TooltipContent>
    </Tooltip>
  );
}
