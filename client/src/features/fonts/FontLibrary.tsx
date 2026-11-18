/** 字体库：左侧按真实字形浏览家族，右侧常驻字样；上传、搜索与详情仍共用原有接口和资源清理。 */
import { useRef, useState } from "react";
import { Search, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { FONT_SLOTS, previewUrl } from "./api";
import { FontDetails } from "./FontDetails";
import { FontSpecimen, type FaceLoader } from "./FontSpecimen";
import type { FontFaceInfo } from "./api";
import { cn } from "@/lib/utils";
import { FontName } from "./FontName";
import { SLOT_NAMES, SOURCES, sizeText } from "./format";
import { useFonts, type Previewer } from "./useFonts";

/** 字体库页面；`previewer` 与 `loader` 供测试替换预览条的绘制与字样的字体文件注册。 */
export function FontLibrary({ previewer, loader }: { previewer?: Previewer; loader?: FaceLoader }) {
  const { fonts, loadError, problem, uploading, upload, reload } = useFonts();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  // 家族内的字形与详情共用右栏；列表、右栏各自滚动，页签切换卸载旧字形。
  const [page, setPage] = useState<FontFaceInfo["slot"] | "details">("regular");
  const input = useRef<HTMLInputElement>(null);
  const needle = query.trim().toLowerCase();
  const shown = (fonts ?? []).filter((font) => !needle || `${font.label} ${font.family} ${font.style}`.toLowerCase().includes(needle));
  const current = shown.find((font) => font.family === selected) ?? shown[0];
  const face = current?.faces.find((face) => face.slot === page) ?? current?.faces[0];
  const activePage = page === "details" ? "details" : face?.slot ?? "regular";
  /** 上传一个字体；成功后字库已刷新，用轻提示告知（库里本来就有这个文件时说明已在库里，上传前修过文件时一并说明），失败的原因显示在页面上。 */
  async function addFont(file: File) {
    const uploaded = await upload(file, previewer);
    if (input.current) input.current.value = "";
    if (!uploaded) return;
    const fixed = uploaded.repairs.length ? `（已自动修复字体文件：${uploaded.repairs.join("；")}）` : "";
    toast.success(`${uploaded.created ? `已添加字体：${uploaded.font.family}` : `字体已在字体库里：${uploaded.font.family}`}${fixed}`);
  }
  return (
    <section aria-label="字体库" className="paper-surface relative mx-auto flex h-full max-w-[1600px] flex-col overflow-hidden px-5 py-6 sm:px-10">
      <header className="mb-5 flex shrink-0 items-center justify-between gap-4 border-b border-line pb-6">
        <div className="flex items-baseline gap-4"><h1 className="sketch-text text-[40px]">字体库</h1><span className="hidden text-xs tracking-[.18em] text-fg-subtle sm:inline">FONT LIBRARY</span></div>
        <div className="flex items-center gap-3">
          <span className="hidden text-xs text-fg-muted sm:inline">.ttf / .otf</span>
          <input ref={input} type="file" accept=".ttf,.otf,font/ttf,font/otf" aria-label="选择字体文件" className="sr-only" tabIndex={-1}
            onChange={(event) => { const file = event.target.files?.[0]; if (file) void addFont(file); }} />
          <Button type="button" variant="outline" disabled={uploading} onClick={() => input.current?.click()}>
            {uploading ? <Spinner /> : <Upload aria-hidden="true" />}{uploading ? "正在上传…" : "上传字体"}
          </Button>
        </div>
      </header>
      {problem && <p role="alert" className="mb-4 text-sm text-destructive">{problem}</p>}
      {loadError && <div role="alert" className="mb-4 flex items-center gap-3 text-sm text-destructive"><span>{loadError}</span><Button type="button" variant="outline" size="sm" onClick={reload}>重试</Button></div>}
      <div className="grid min-h-0 flex-1 gap-7 overflow-y-auto lg:grid-cols-[minmax(240px,30%)_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:gap-10 lg:overflow-hidden">
        <div className="flex min-h-0 min-w-0 flex-col lg:border-r lg:border-line lg:pr-7">
          <div className="relative shrink-0">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索字体名称或风格" aria-label="搜索字体" className="pl-8" />
          </div>
          <p className="my-4 shrink-0 text-xs text-muted-foreground" role="status">{fonts ? `共 ${fonts.length} 个字体${needle ? `，匹配 ${shown.length} 个` : ""}` : ""}</p>
          {!fonts && !loadError && <div className="space-y-3" aria-label="正在读取字体库">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-24 rounded-sm" />)}</div>}
          {fonts && shown.length === 0 && <Empty><EmptyHeader><EmptyTitle>没有匹配的字体</EmptyTitle><EmptyDescription>换一个名称或风格关键词试试。</EmptyDescription></EmptyHeader></Empty>}
          <ul aria-label="字体家族" className="relative max-h-[40dvh] min-h-0 overflow-y-auto lg:max-h-none lg:flex-1">
            {shown.map((font) => <li key={font.family}>
              <button type="button" aria-label={`选择字体 ${font.label}`} aria-pressed={current?.family === font.family}
                onClick={() => { setSelected(font.family); setPage("regular"); }}
                className={cn("relative w-full border-l-2 border-transparent px-4 py-5 text-left transition-colors hover:bg-raised", current?.family === font.family && "border-l-fg bg-raised/70")}>
                <FontName label={font.label} url={previewUrl(font)} className="h-9 w-full" />
                <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground">{font.family}</p>
                <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                  <span>{font.faces.map((face) => SLOT_NAMES[face.slot]).join(" · ")}</span><span>{SOURCES[font.source]}</span>
                </div>
                <span className="sr-only">{font.style} · {sizeText(font.size)}</span>
              </button>
            </li>)}
          </ul>
        </div>
        <div className="min-h-0 min-w-0 lg:flex lg:flex-col" aria-label="字体预览">
          {current && <Tabs value={activePage} onValueChange={(value) => setPage(value as typeof page)} className="min-h-0 flex-1 gap-0">
            <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
              <div><p className="text-xs text-fg-subtle">字样</p><h2 className="mt-1 text-xl">{current.label}</h2></div>
              <TabsList aria-label="字体页面" className="h-auto flex-wrap justify-start">
                {FONT_SLOTS.map((slot) => <TabsTrigger key={slot} value={slot} disabled={!current.faces.some((face) => face.slot === slot)} title={current.faces.some((face) => face.slot === slot) ? undefined : "未上传此字形"}>{SLOT_NAMES[slot]}</TabsTrigger>)}
                <TabsTrigger value="details">字体详情</TabsTrigger>
              </TabsList>
            </header>
            <TabsContent key={`${current.family}:${activePage}`} value={activePage} className="relative min-h-0 pt-5 lg:overflow-y-auto lg:pr-2">
              {activePage === "details" ? <FontDetails font={current} />
                : face?.file_url ? <FontSpecimen font={current} face={{ ...face, file_url: face.file_url }} loader={loader} />
                : <p className="py-14 text-sm text-fg-muted">本机未安装该字体文件，无法查看字样。</p>}
            </TabsContent>
          </Tabs>}
        </div>
      </div>
      <details className="mt-5 max-h-24 shrink-0 overflow-y-auto border-t border-line pt-3 text-xs leading-6 text-muted-foreground">
        <summary className="cursor-pointer">上传说明：字体共享给所有人，上传后不能删除，请确认使用权限。</summary>
        <p className="mt-2">支持 .ttf 与 .otf，同一个文件只能上传一次。同一家族的常规、粗体、斜体、粗斜体归入同一字体，其他字重单独展示，同一字形的其他版本另列。</p>
      </details>
    </section>
  );
}
