/**
 * 字体下拉选择器：读取共享字体库，每个选项用服务端生成的预览条（黑色透明底 PNG，经 CSS mask 着色）以该字体自己的样子展示，
 * 可搜索；底部可上传自己的字体，上传后立即选中。选项是实时字体库，而不是效果生成时的枚举，所以新上传的字体立刻可选。
 */
import { useId, useRef, useState } from "react";
import { Check, ChevronsUpDown, Upload } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { FontName } from "./FontName";
import { useFonts, type Previewer } from "./useFonts";

/** 选中某个字体时回传其家族名；`previewer` 供测试替换预览条的绘制。 */
export function FontPicker({ id, label, value, disabled, onChange, previewer, className }: {
  id?: string;
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (family: string) => void;
  previewer?: Previewer;
  className?: string;
}) {
  const generated = useId();
  const [open, setOpen] = useState(false);
  const { fonts: library, loadError, problem, uploading, upload } = useFonts();
  const fonts = library ?? [];
  const input = useRef<HTMLInputElement>(null);
  const current = fonts.find((font) => font.family === value);
  /** 上传成功后选中新字体并关闭浮层；失败的原因留在浮层里。 */
  async function addFont(file: File) {
    const created = await upload(file, previewer);
    if (input.current) input.current.value = "";
    if (!created) return;
    onChange(created.family);
    setOpen(false);
  }
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id ?? generated}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={label}
          disabled={disabled}
          data-slot="select-trigger"
          className={cn("flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none transition-[color,box-shadow] hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50", className)}
        >
          <span className="min-w-0 flex-1 truncate text-left">{current ? <FontName font={current} /> : value}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="template-inspector-select w-(--radix-popover-trigger-width) min-w-72 p-0">
        <Command>
          <CommandInput placeholder="搜索字体" aria-label="搜索字体" />
          <CommandList>
            <CommandEmpty>没有匹配的字体</CommandEmpty>
            <CommandGroup>
              {fonts.map((font) => (
                // cmdk 以 value 区分选项并参与过滤：名称、家族名与风格都能搜到。
                <CommandItem key={font.id} value={`${font.label} ${font.family} ${font.style} ${font.id}`} onSelect={() => { onChange(font.family); setOpen(false); }}>
                  <span className="min-w-0 flex-1">
                    <FontName font={font} />
                    <span className="block truncate text-[11px] text-muted-foreground">{font.family}{font.source === "upload" ? " · 上传" : ""}</span>
                  </span>
                  <Check className={cn("size-3.5 shrink-0", font.family === value ? "opacity-100" : "opacity-0")} aria-hidden="true" />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
        <div className="border-t p-2">
          <input ref={input} type="file" accept=".ttf,.otf,font/ttf,font/otf" aria-label="选择字体文件" className="sr-only" tabIndex={-1}
            onChange={(event) => { const file = event.target.files?.[0]; if (file) void addFont(file); }} />
          <button type="button" disabled={uploading} onClick={() => input.current?.click()}
            className="flex h-8 w-full items-center justify-center gap-1.5 rounded-md text-xs font-medium hover:bg-accent disabled:opacity-50">
            {uploading ? <Spinner className="size-3.5" /> : <Upload className="size-3.5" aria-hidden="true" />}
            {uploading ? "正在上传…" : "上传字体（.ttf / .otf）"}
          </button>
          <p className="mt-1 px-1 text-[11px] leading-4 text-muted-foreground">上传的字体会加入所有人共享的字体库，请确认你有权使用该字体。</p>
          {(problem ?? loadError) && <p role="alert" className="mt-1 px-1 text-xs text-destructive">{problem ?? loadError}</p>}
        </div>
      </PopoverContent>
    </Popover>
  );
}
