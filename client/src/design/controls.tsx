/**
 * 设计沙盒的表单控件，主要给编辑页的属性面板使用：分段选择、开关、滑杆、数值输入、下拉选择，以及属性分组与属性行。
 * 交互语义交给 Radix 原语；外观统一为紧凑的 32px 行高，标签在左、控件在右，与 Figma 属性面板的密度相近。
 */
import { useId, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import { Popover, Slider as SliderPrimitive, Switch as SwitchPrimitive, ToggleGroup } from "radix-ui";
import { cn } from "@/lib/utils";
import { Icon } from "./icons";

/** 分段选择：互斥选项，选中块在选项之间滑动；size="sm" 用于面板里的紧凑场景。 */
export function Segmented<T extends string>({ value, options, onChange, label, size = "md", className }: {
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean }[];
  onChange: (value: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const id = useId();
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(next) => next && onChange(next as T)}
      aria-label={label}
      className={cn("flex rounded-lg bg-raised p-0.5", className)}
    >
      {options.map((option) => (
        <ToggleGroup.Item
          key={option.value}
          value={option.value}
          disabled={option.disabled}
          className={cn(
            "relative flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-fg-muted transition-colors hover:text-fg disabled:opacity-40 data-[state=on]:text-fg [&_svg]:size-3.5",
            size === "sm" ? "h-6 px-2 text-[11.5px]" : "h-7 px-2.5 text-xs",
          )}
        >
          {option.value === value && (
            <motion.span layoutId={id} transition={{ type: "spring", stiffness: 520, damping: 38 }} className="absolute inset-0 rounded-md bg-surface shadow-[0_1px_2px_oklch(0.2_0.01_265/0.12)] ring-1 ring-line" />
          )}
          <span className="relative flex items-center gap-1.5">{option.label}</span>
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

/** 开关：开启时为强调色。 */
export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) {
  return (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onChange}
      aria-label={label}
      className="relative h-5 w-9 shrink-0 rounded-full bg-hover transition-colors data-[state=checked]:bg-accent"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-[0_1px_3px_oklch(0_0_0/0.25)] transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  );
}

/** 滑杆：细轨道加白色圆钮，已选范围为强调色。 */
export function Slider({ value, min, max, step = 1, onChange, label, disabled }: { value: number; min: number; max: number; step?: number; onChange: (value: number) => void; label: string; disabled?: boolean }) {
  return (
    <SliderPrimitive.Root
      value={[value]}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={([next]) => onChange(next)}
      aria-label={label}
      className="relative flex h-5 w-full touch-none select-none items-center data-[disabled]:opacity-40"
    >
      <SliderPrimitive.Track className="relative h-1 grow overflow-hidden rounded-full bg-hover">
        <SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb aria-label={label} className="block size-3.5 rounded-full bg-white shadow-[0_1px_3px_oklch(0_0_0/0.3)] ring-1 ring-line-strong transition-transform hover:scale-110 focus-visible:outline-2 focus-visible:outline-accent" />
    </SliderPrimitive.Root>
  );
}

/**
 * 数值输入：等宽数字右对齐，可带前缀（如 X、Y）与单位；编辑时保留原文，失焦或回车时按范围钳制后提交，
 * 不是数字则恢复原值。
 */
export function NumberField({ value, onChange, min, max, step = 1, prefix, unit, label, disabled, className }: {
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  prefix?: string;
  unit?: string;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const parsed = Number(draft);
    if (draft.trim() !== "" && Number.isFinite(parsed)) onChange(Math.min(max, Math.max(min, Math.round(parsed / step) * step)));
    setDraft(null);
  };
  return (
    <label className={cn("flex h-8 min-w-0 items-center gap-1 rounded-lg border border-line bg-sunken px-2 text-xs transition-[border-color,box-shadow] focus-within:border-accent/60 focus-within:ring-[3px] focus-within:ring-accent/15", disabled && "opacity-45", className)}>
      {prefix && <span className="w-3 shrink-0 text-[11px] font-medium text-fg-subtle">{prefix}</span>}
      <input
        aria-label={label}
        inputMode="decimal"
        disabled={disabled}
        value={draft ?? String(Math.round(value * 100) / 100)}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => event.key === "Enter" && commit()}
        className="min-w-0 flex-1 bg-transparent text-right font-mono tabular-nums text-fg outline-none"
      />
      {unit && <span className="shrink-0 text-[11px] text-fg-subtle">{unit}</span>}
    </label>
  );
}

/**
 * 下拉选择：触发按钮显示当前项，弹出列表可选；renderOption 可以为每项画预览（如字体、花字缩略图）。
 * 选项多时列表内部滚动。
 */
export function SelectMenu<T extends string>({ value, options, onChange, label, renderOption, placeholder = "请选择", className }: {
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  renderOption?: (option: { value: T; label: string }) => ReactNode;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((option) => option.value === value);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        aria-label={label}
        className={cn("flex h-8 w-full min-w-0 items-center gap-2 rounded-lg border border-line bg-sunken px-2.5 text-left text-xs text-fg transition-colors hover:border-line-strong data-[state=open]:border-accent/60 data-[state=open]:ring-[3px] data-[state=open]:ring-accent/15", className)}
      >
        <span className={cn("min-w-0 flex-1 truncate", !current && "text-fg-subtle")}>{current ? (renderOption ? renderOption(current) : current.label) : placeholder}</span>
        <Icon name="alt-arrow-down" className="size-3.5 shrink-0 text-fg-subtle" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className="z-[70] max-h-72 w-[var(--radix-popover-trigger-width)] overflow-y-auto rounded-xl border border-line-strong bg-overlay p-1 shadow-pop data-[state=open]:animate-pop-in">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
              className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-raised", option.value === value ? "text-accent" : "text-fg")}
            >
              <span className="min-w-0 flex-1 truncate">{renderOption ? renderOption(option) : option.label}</span>
              {option.value === value && <Icon name="check-circle" variant="duotone" className="size-3.5 shrink-0" />}
            </button>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** 属性分组：小标题加内容，分组之间以细线分隔；action 放在标题右侧（如重置、开关）。 */
export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2.5 border-b border-line px-4 py-4 last:border-b-0">
      <header className="flex h-5 items-center justify-between gap-2">
        <h3 className="text-[11.5px] font-medium text-fg-muted">{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

/** 属性行：左侧固定宽度的标签，右侧控件。 */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[56px_minmax(0,1fr)] items-center gap-2">
      <span className="truncate text-xs text-fg-muted">{label}</span>
      <div className="flex min-w-0 items-center gap-2">{children}</div>
    </div>
  );
}
