/**
 * 工作室界面的对话框外壳：模糊遮罩、居中浮层、标题区与底部操作栏。
 * 焦点管理、Esc 关闭与读屏语义由 Radix Dialog 提供；内容与按钮由调用方组合。
 */
import type { ReactNode } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { Icon } from "./icons";
import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;

/** 浮层主体；busy 时禁止点击遮罩或按 Esc 关闭，避免请求进行中被中断。 */
export function DialogContent({ children, busy, className }: { children: ReactNode; busy?: boolean; className?: string }) {
  const block = (event: Event) => {
    if (busy) event.preventDefault();
  };
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-scrim backdrop-blur-[3px] data-[state=closed]:animate-fade-out data-[state=open]:animate-fade-in" />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        onEscapeKeyDown={block}
        onPointerDownOutside={block}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-[20px] border border-line-strong bg-overlay shadow-pop focus:outline-none data-[state=closed]:animate-dialog-out data-[state=open]:animate-dialog-in",
          className,
        )}
      >
        {children}
        <DialogPrimitive.Close
          disabled={busy}
          aria-label="关闭"
          className="absolute right-4 top-4 inline-flex size-8 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-raised hover:text-fg disabled:opacity-40"
        >
          <Icon name="close" className="size-[18px]" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/** 标题区：左侧图标块标明对话框性质（tone 决定颜色），右侧标题与说明。 */
export function DialogHeader({ icon, tone = "neutral", title, description }: { icon: ReactNode; tone?: "neutral" | "brand" | "danger"; title: string; description?: ReactNode }) {
  const tones = {
    neutral: "bg-raised text-fg-muted",
    brand: "bg-brand/15 text-brand",
    danger: "bg-danger/15 text-danger",
  };
  return (
    <div className="flex gap-4 px-6 pb-2 pt-6 pr-14">
      <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-xl [&_svg]:size-5", tones[tone])}>{icon}</span>
      <div className="min-w-0 pt-0.5">
        <DialogPrimitive.Title className="text-[17px] font-semibold leading-tight tracking-tight">{title}</DialogPrimitive.Title>
        {/* 没有说明时仍渲染隐藏描述，满足 Radix 对 aria-describedby 的检查。 */}
        <DialogPrimitive.Description className={description ? "mt-1.5 text-[13px] leading-relaxed text-fg-muted" : "sr-only"}>
          {description ?? title}
        </DialogPrimitive.Description>
      </div>
    </div>
  );
}

/** 底部操作栏：与正文以细线分隔，按钮右对齐，主操作放最右。 */
export function DialogFooter({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="mt-2 flex flex-wrap items-center justify-end gap-2 border-t border-line px-6 py-4">
      {note && <div className="mr-auto min-w-0 text-xs text-fg-subtle">{note}</div>}
      {children}
    </div>
  );
}
