/**
 * 设计沙盒的基础控件：按钮、图标按钮、悬停提示、骨架块、呼吸圆点、加载圈与带字数统计的输入框。
 * 交互语义交给 Radix 原语，这里只负责视觉，颜色全部取自 design.css 的令牌；不请求数据、不依赖页面。
 */
import { useId, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

/**
 * 按钮层级：primary 是一屏里最主要的操作，用墨色底加由上到下的细微亮度渐变、顶部内高光与柔和投影，按下时轻微下沉；
 * 禁用时退成中性浅灰而不是调淡，避免出现发灰的色块。secondary 用于并列操作，ghost 用于工具栏，danger 只用于确认删除。
 */
const buttonVariants = {
  primary:
    "border border-ink bg-ink bg-linear-to-b from-white/[0.14] to-transparent font-medium text-on-ink shadow-[inset_0_1px_0_oklch(1_0_0/0.16),0_1px_2px_oklch(0.2_0.01_265/0.2),0_6px_14px_-6px_oklch(0.2_0.01_265/0.4)] hover:bg-ink-hover active:translate-y-px active:shadow-[inset_0_1px_0_oklch(1_0_0/0.1),0_1px_1px_oklch(0.2_0.01_265/0.2)] disabled:border-line disabled:bg-raised disabled:bg-none disabled:text-fg-subtle disabled:opacity-100 disabled:shadow-none",
  secondary: "border border-line-strong bg-raised text-fg hover:bg-hover",
  ghost: "text-fg-muted hover:bg-raised hover:text-fg",
  danger: "bg-danger font-medium text-white hover:brightness-110",
};

const buttonSizes = {
  sm: "h-8 gap-1.5 rounded-lg px-3 text-[13px] [&_svg]:size-3.5",
  md: "h-10 gap-2 rounded-[10px] px-4 text-sm [&_svg]:size-4",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof buttonVariants;
  size?: keyof typeof buttonSizes;
}

/** 文字按钮；禁用时降低不透明度并保留占位，避免布局跳动。 */
export function Button({ variant = "secondary", size = "md", className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap transition-[background-color,filter,color,border-color,box-shadow,translate] duration-150 disabled:pointer-events-none disabled:opacity-45",
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    />
  );
}

/** 悬停提示：浅色气泡在深色界面上最醒目；只给纯图标按钮或需要补充信息的控件使用。 */
export function Tooltip({ label, side = "top", children }: { label: ReactNode; side?: "top" | "right" | "bottom" | "left"; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={8}
          className="z-[70] max-w-64 rounded-md bg-fg px-2.5 py-1.5 text-xs leading-snug text-canvas shadow-lift data-[state=closed]:animate-fade-out data-[state=delayed-open]:animate-pop-in data-[state=instant-open]:animate-pop-in"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

const iconVariants = {
  ghost: "text-fg-subtle hover:bg-raised hover:text-fg",
  glass: "bg-black/45 text-white/85 backdrop-blur-md hover:bg-black/65 hover:text-white",
  "glass-danger": "bg-black/45 text-white/85 backdrop-blur-md hover:bg-danger hover:text-white",
};

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  variant?: keyof typeof iconVariants;
}

/** 纯图标按钮：label 同时作为无障碍名称与悬停提示。glass 变体用于压在封面画面上的操作。 */
export function IconButton({ label, variant = "ghost", className, type = "button", ...props }: IconButtonProps) {
  return (
    <Tooltip label={label}>
      <button
        type={type}
        aria-label={label}
        className={cn("inline-flex size-8 shrink-0 items-center justify-center rounded-lg transition-colors duration-150 disabled:opacity-40 [&_svg]:size-4", iconVariants[variant], className)}
        {...props}
      />
    </Tooltip>
  );
}

/** 骨架占位：底色块上扫过一道高光，形状由调用方用尺寸与圆角类给出。 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn("relative block overflow-hidden rounded-md bg-raised", className)}>
      <span className="absolute inset-0 animate-shimmer bg-linear-to-r from-transparent via-shimmer to-transparent" />
    </span>
  );
}

/** 呼吸圆点：实心点外扩散一圈光环，表示「有待处理的状态」；颜色取 currentColor，由调用方的文字色决定。 */
export function PulseDot({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn("relative inline-flex size-2 shrink-0 rounded-full", className)}>
      <span className="absolute inset-0 animate-ping-soft rounded-full bg-current" />
      <span className="relative size-full rounded-full bg-current" />
    </span>
  );
}

/** 加载圈：一段弧线匀速旋转，尺寸与颜色跟随文字。 */
export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={cn("size-4 animate-spin", className)}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.5" />
    </svg>
  );
}

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  maxLength: number;
  required?: boolean;
  multiline?: boolean;
  placeholder?: string;
  error?: string;
  autoFocus?: boolean;
}

/**
 * 带标签、字数统计与错误提示的输入框。超过上限由 maxLength 直接阻止；
 * 接近上限时计数变为警示色，错误信息通过 aria-describedby 关联给读屏。
 */
export function TextField({ label, value, onChange, maxLength, required, multiline, placeholder, error, autoFocus }: TextFieldProps) {
  const id = useId();
  const nearLimit = value.length > maxLength * 0.9;
  const control = cn(
    "w-full rounded-[10px] border bg-sunken px-3 text-sm text-fg outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-fg-subtle focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15",
    error ? "border-danger/60" : "border-line",
  );
  const shared = {
    id,
    value,
    maxLength,
    placeholder,
    autoFocus,
    "aria-invalid": Boolean(error) || undefined,
    "aria-describedby": error ? `${id}-error` : undefined,
  };
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-[13px] font-medium text-fg">
          {label}
          {required ? <span className="ml-1 text-accent">*</span> : <span className="ml-1.5 font-normal text-fg-subtle">选填</span>}
        </label>
        <span className={cn("font-mono text-[11px] tabular-nums", nearLimit ? "text-warning" : "text-fg-subtle")}>
          {value.length}/{maxLength}
        </span>
      </div>
      {multiline ? (
        <textarea {...shared} rows={3} onChange={(event) => onChange(event.target.value)} className={cn(control, "min-h-[84px] resize-none py-2.5 leading-relaxed")} />
      ) : (
        <input {...shared} onChange={(event) => onChange(event.target.value)} className={cn(control, "h-10")} />
      )}
      {error && <p id={`${id}-error`} className="text-xs text-danger">{error}</p>}
    </div>
  );
}
