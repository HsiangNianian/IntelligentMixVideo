/** 字体选择的共用界面：搜索名称和标识、展示字形预览与选中状态；目录和附加操作由调用方提供。 */
import { useId, useState, type ReactNode } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { FontName } from "./FontName";

/** 字体展示数据；value 为业务保存值，预览条地址由所属字体目录解析。 */
export interface FontOption {
  value: string;
  label: string;
  previewUrl: string | null;
  description: string;
  keywords?: string;
}

/** 选中后关闭浮层，附加操作可调用 close；关闭时搜索内容随浮层卸载。 */
export function FontSelect({ id, label, value, options, disabled, onChange, className, footer }: {
  id?: string;
  label: string;
  value: string;
  options: FontOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
  className?: string;
  footer?: (close: () => void) => ReactNode;
}) {
  const generated = useId();
  const [open, setOpen] = useState(false);
  const current = options.find((font) => font.value === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button id={id ?? generated} type="button" role="combobox" aria-expanded={open} aria-label={label}
          disabled={disabled} data-slot="select-trigger"
          className={cn("flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none transition-[color,box-shadow] hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50", className)}>
          <span className="min-w-0 flex-1 truncate text-left">{current ? <FontName label={current.label} url={current.previewUrl} /> : value}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="template-inspector-select w-(--radix-popover-trigger-width) min-w-72 p-0">
        <Command>
          <CommandInput placeholder="搜索字体" aria-label="搜索字体" />
          <CommandList>
            <CommandEmpty>没有匹配的字体</CommandEmpty>
            <CommandGroup>
              {options.map((font) => (
                <CommandItem key={font.value} value={`${font.label} ${font.value} ${font.description} ${font.keywords ?? ""}`}
                  onSelect={() => { onChange(font.value); setOpen(false); }}>
                  <span className="min-w-0 flex-1">
                    <FontName label={font.label} url={font.previewUrl} />
                    <span className="block truncate text-[11px] text-muted-foreground">{font.description}</span>
                  </span>
                  <Check className={cn("size-3.5 shrink-0", font.value === value ? "opacity-100" : "opacity-0")} aria-hidden="true" />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
        {footer?.(() => setOpen(false))}
      </PopoverContent>
    </Popover>
  );
}
