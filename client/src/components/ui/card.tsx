/**
 * shadcn/ui Card 基础容器：合并主题样式与调用方属性，不包含业务状态。
 * 按需保留 Card，来源 https://ui.shadcn.com/r/styles/new-york-v4/card.json。
 * Copyright (c) 2023 shadcn — MIT，完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** 提供可组合的卡片外观，透传 div 属性以支持无障碍与布局。 */
export function Card({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card"
      className={cn(
        "flex flex-col gap-6 rounded-xl border bg-card py-6 text-card-foreground shadow-sm",
        className,
      )}
      {...props}
    />
  );
}
