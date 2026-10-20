/**
 * 字体库页面：以卡片展示共享字体库里的全部字体（系统默认、预装与用户上传），每张卡片用服务端预览条以字体自己的样子显示名称，
 * 并给出家族名、风格与字重；可按名称或风格搜索，也可以在这里上传自己的 .ttf/.otf。页面每次打开都重新读取字体库。
 */
import { useRef, useState } from "react";
import { Search, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "sonner";
import { previewUrl, type FontInfo } from "./api";
import { FontName } from "./FontName";
import { useFonts, type Previewer } from "./useFonts";

/** 字体来源的中文说明。 */
const SOURCES: Record<FontInfo["source"], string> = { system: "系统默认", builtin: "预装", upload: "用户上传" };

/** 字重的显示：单一字重写数字，可变字体写范围。 */
const weightText = (weights: number[]) => (weights.length === 2 ? `${weights[0]}–${weights[1]}（可变）` : String(weights[0]));

/** 文件大小的简短显示；系统默认字体没有文件。 */
const sizeText = (font: FontInfo) => (font.size > 0 ? `${(font.size / 1024 / 1024).toFixed(1)} MB` : "随系统安装");

/** 字体库页面；`previewer` 供测试替换预览条的绘制。 */
export function FontLibrary({ previewer }: { previewer?: Previewer }) {
  const { fonts, loadError, problem, uploading, upload, reload } = useFonts();
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const needle = query.trim().toLowerCase();
  const shown = (fonts ?? []).filter((font) => !needle || `${font.label} ${font.family} ${font.style}`.toLowerCase().includes(needle));
  /** 上传一个字体；成功后字库已刷新，用轻提示告知，失败的原因显示在页面上。 */
  async function addFont(file: File) {
    const created = await upload(file, previewer);
    if (input.current) input.current.value = "";
    if (created) toast.success(`已添加字体：${created.family}`);
  }
  return (
    <section aria-label="字体库" className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-52 flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索字体名称或风格" aria-label="搜索字体" className="pl-8" />
        </div>
        <p className="text-xs text-muted-foreground" role="status">{fonts ? `共 ${fonts.length} 个字体${needle ? `，匹配 ${shown.length} 个` : ""}` : ""}</p>
        <input ref={input} type="file" accept=".ttf,.otf,font/ttf,font/otf" aria-label="选择字体文件" className="sr-only" tabIndex={-1}
          onChange={(event) => { const file = event.target.files?.[0]; if (file) void addFont(file); }} />
        <Button type="button" className="ml-auto" disabled={uploading} onClick={() => input.current?.click()}>
          {uploading ? <Spinner /> : <Upload aria-hidden="true" />}
          {uploading ? "正在上传…" : "上传字体"}
        </Button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        支持 .ttf 与 .otf。上传的字体会加入所有人共享的字体库，之后不能删除，请确认你有权使用该字体；同一个文件只能上传一次，同名字体的其他字重或版本会另起一个家族名。
      </p>
      {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
      {loadError && (
        <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
          <span>{loadError}</span>
          <Button type="button" variant="outline" size="sm" onClick={reload}>重试</Button>
        </div>
      )}
      {!fonts && !loadError && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3" aria-label="正在读取字体库">
          {Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-32 rounded-lg" />)}
        </div>
      )}
      {fonts && shown.length === 0 && (
        <Empty><EmptyHeader><EmptyTitle>没有匹配的字体</EmptyTitle><EmptyDescription>换一个名称或风格关键词试试。</EmptyDescription></EmptyHeader></Empty>
      )}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
        {shown.map((font) => (
          <li key={font.id} className="flex flex-col gap-2 rounded-lg border bg-card p-4 text-card-foreground">
            <FontName label={font.label} url={previewUrl(font)} className="h-9 w-full" />
            <div className="min-w-0">
              <p className="truncate font-mono text-xs text-muted-foreground">{font.family}</p>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{font.style}</p>
            </div>
            <dl className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <dt className="sr-only">来源</dt>
              <dd className="rounded bg-accent px-1.5 py-0.5 text-foreground">{SOURCES[font.source]}</dd>
              <dt className="sr-only">字重</dt>
              <dd>字重 {weightText(font.weights)}</dd>
              <dt className="sr-only">大小</dt>
              <dd>{sizeText(font)}</dd>
            </dl>
          </li>
        ))}
      </ul>
    </section>
  );
}
