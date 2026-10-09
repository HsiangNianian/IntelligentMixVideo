/** ObsidianUI spinner 基础组件（基于 shadcn/ui）：旋转的加载图标。
 * 与上游不同，默认作为装饰（aria-hidden），加载状态文字由调用方的 role="status" 元素提供，避免重复播报。
 * Copyright (c) 2026 ObsidianUI、(c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import type { ComponentProps } from "react";
import { Loader2Icon } from "lucide-react";
import { cn } from "@/lib/utils";

/** Spinner 默认 16px；减少动态效果时停止旋转。 */
function Spinner({ className, ...props }: ComponentProps<"svg">) {
  return (
    <Loader2Icon
      aria-hidden="true"
      className={cn("size-4 animate-spin motion-reduce:animate-none", className)}
      {...props}
    />
  );
}

export { Spinner };
