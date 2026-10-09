/** ObsidianUI skeleton 基础组件（基于 shadcn/ui）：内容加载前的脉冲占位块，尺寸由调用方指定。
 * Copyright (c) 2026 ObsidianUI、(c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** Skeleton 只负责占位外观；读屏提示由所在区域的状态文字提供。 */
function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-accent motion-reduce:animate-none", className)}
      {...props}
    />
  );
}

export { Skeleton };
