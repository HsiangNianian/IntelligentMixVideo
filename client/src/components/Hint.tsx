/** 图标按钮的悬停提示：把 Tooltip 的触发器、内容组合成一层，替代浏览器原生 title 提示；文字标签可见时不渲染提示。 */
import { useSyncExternalStore, type ComponentProps, type ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** 与 Tailwind 默认断点一致的最小宽度查询。 */
const breakpoints = { sm: "(min-width: 40rem)", md: "(min-width: 48rem)" };

/** 订阅媒体查询变化；未传查询时恒为 false。 */
function useMediaQuery(query: string | undefined) {
  return useSyncExternalStore(
    (notify) => {
      if (!query) return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => (query ? window.matchMedia(query).matches : false),
  );
}

/**
 * children 必须是单个元素（通常是 Button），其无障碍名称仍由自身 aria-label 提供。
 * Tooltip 会在触发元素上写入 data-state，因此依赖 data-state 的元素（如 TabsTrigger）需先包一层 div 再传入。
 * visibleBelow 表示只在该断点以下（文字被隐藏时）提供提示，disabled 表示当前文字可见、不需要提示。
 * 不需要时直接返回 children，不保留隐藏的提示层，避免它截获 Esc 或让读屏器重复朗读。
 */
export function Hint({ label, side = "top", visibleBelow, disabled = false, children }: {
  label: string;
  side?: ComponentProps<typeof TooltipContent>["side"];
  visibleBelow?: keyof typeof breakpoints;
  disabled?: boolean;
  children: ReactElement;
}) {
  const wide = useMediaQuery(visibleBelow && breakpoints[visibleBelow]);
  if (disabled || wide) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
}
