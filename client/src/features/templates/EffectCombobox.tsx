/** 可搜索的特效下拉框：Popover + Command 组合，供参数栏的花字、气泡、滤镜、动画和转场选择复用。 */
import { useState, type ReactNode } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** 单个选项；hint 为右侧的辅助信息（如效果编号），始终参与搜索，名称已包含它时不重复显示。 */
export interface ComboboxOption {
  value: string;
  label: string;
  hint?: string;
}

/**
 * 触发器默认是与 Select 一致的输入样式（共用 select-trigger 槽位，参数栏样式同样生效），children 可替换为自定义卡片。
 * 选中后关闭浮层；搜索词在浮层关闭时随内容一起卸载，下次打开重新开始。
 */
export function EffectCombobox({ id, value, options, placeholder, searchPlaceholder, disabled, onChange, ariaLabel, className, children }: {
  id?: string;
  value: string;
  options: ComboboxOption[];
  placeholder: string;
  searchPlaceholder: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  ariaLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((option) => option.value === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button id={id} type="button" role="combobox" data-slot={children ? undefined : "select-trigger"} aria-expanded={open} aria-label={ariaLabel} disabled={disabled}
          className={cn(children ? "w-full text-left" : "flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none transition-[color,box-shadow] hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50", className)}>
          {children ?? <>
            <span className={cn("truncate", !current && "text-muted-foreground")}>{current?.label ?? placeholder}</span>
            <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          </>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="template-inspector-select w-(--radix-popover-trigger-width) min-w-60 p-0">
        <Command>
          <CommandInput placeholder={searchPlaceholder} aria-label={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>没有匹配的结果</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                // cmdk 以 value 区分选项并参与过滤，拼入原始值避免同名效果冲突。
                <CommandItem key={option.value} value={`${option.label} ${option.hint ?? ""} ${option.value}`} onSelect={() => { onChange(option.value); setOpen(false); }}>
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {option.hint && !option.label.includes(option.hint) && <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{option.hint}</span>}
                  <Check className={cn("size-3.5 shrink-0", option.value === value ? "opacity-100" : "opacity-0")} aria-hidden="true" />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
