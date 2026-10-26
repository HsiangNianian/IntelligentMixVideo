/** Remotion 字体选择：读取共享字体库并接入共用预览选择器，上传成功后立即选中。 */
import { useRef } from "react";
import { Upload } from "lucide-react";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "sonner";
import { FontSelect } from "./FontSelect";
import { previewUrl } from "./api";
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
  const { fonts: library, loadError, problem, uploading, upload } = useFonts();
  const input = useRef<HTMLInputElement>(null);
  /** 上传成功（或库里本来就有这个文件）后选中该字体并关闭浮层；失败的原因留在浮层里，上传前修过文件时用轻提示说明。 */
  async function addFont(file: File, close: () => void) {
    const uploaded = await upload(file, previewer);
    if (input.current) input.current.value = "";
    if (!uploaded) return;
    if (uploaded.repairs.length) toast.info(`已自动修复字体文件后上传：${uploaded.repairs.join("；")}`);
    onChange(uploaded.font.family);
    close();
  }
  return (
    <FontSelect id={id} label={label} value={value} disabled={disabled} onChange={onChange} className={className}
      options={(library ?? []).map((font) => ({
        value: font.family, label: font.label, previewUrl: previewUrl(font), keywords: font.style,
        description: `${font.family}${font.source === "upload" ? " · 上传" : ""}`,
      }))}
      footer={(close) => (
        <div className="border-t p-2">
          <input ref={input} type="file" accept=".ttf,.otf,font/ttf,font/otf" aria-label="选择字体文件" className="sr-only" tabIndex={-1}
            onChange={(event) => { const file = event.target.files?.[0]; if (file) void addFont(file, close); }} />
          <button type="button" disabled={uploading} onClick={() => input.current?.click()}
            className="flex h-8 w-full items-center justify-center gap-1.5 rounded-md text-xs font-medium hover:bg-accent disabled:opacity-50">
            {uploading ? <Spinner className="size-3.5" /> : <Upload className="size-3.5" aria-hidden="true" />}
            {uploading ? "正在上传…" : "上传字体（.ttf / .otf）"}
          </button>
          <p className="mt-1 px-1 text-[11px] leading-4 text-muted-foreground">上传的字体会加入所有人共享的字体库，请确认你有权使用该字体。</p>
          {(problem ?? loadError) && <p role="alert" className="mt-1 px-1 text-xs text-destructive">{problem ?? loadError}</p>}
        </div>
      )}
    />
  );
}
