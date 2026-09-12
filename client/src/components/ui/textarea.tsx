/** shadcn/ui textarea 基础组件，不依赖业务逻辑。
 * Copyright (c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import * as React from "react";
import { cn } from "@/lib/utils";

/** 透传多行输入属性，样式遵循主题令牌。 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:aria-invalid:ring-destructive/40",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
