/** shadcn/ui label 基础组件，不依赖业务逻辑。
 * Copyright (c) 2023 shadcn — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import * as React from "react";
import { cn } from "@/lib/utils";
import { Label as LabelPrimitive } from "radix-ui";

/** 关联输入标签，保留 Radix 无障碍语义。 */
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Label };
