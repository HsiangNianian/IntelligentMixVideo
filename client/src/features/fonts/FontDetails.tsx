/** 字体家族信息页：展示来源、许可和风格说明；各字形的字样与文件信息由相邻页签展示。 */
import type { FontInfo } from "./api";
import { SOURCES, sizeText, weightText } from "./format";

/** 只读取当前家族的元数据，不加载字体文件，也不打开弹窗。 */
export function FontDetails({ font }: { font: FontInfo }) {
  return <section aria-label="字体家族信息" className="space-y-6 py-2">
    <p className="text-sm leading-7 text-muted-foreground">{font.style}</p>
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-4 text-sm">
      <dt className="text-muted-foreground">家族名</dt><dd className="break-all font-mono">{font.family}</dd>
      <dt className="text-muted-foreground">来源</dt><dd>{SOURCES[font.source]}</dd>
      <dt className="text-muted-foreground">许可证</dt><dd>{font.license || "未提供"}</dd>
      <dt className="text-muted-foreground">字重</dt><dd>{weightText(font.weights)}</dd>
      <dt className="text-muted-foreground">总大小</dt><dd>{sizeText(font.size)}</dd>
    </dl>
    {font.source === "upload" && <p className="text-xs leading-6 text-muted-foreground">同一家族的常规、粗体、斜体、粗斜体文件上传后自动归入此家族。各字形的文件信息与字样在上方对应页签查看。</p>}
  </section>;
}
