/**
 * 字体详情：点击字体库卡片打开的对话框。列出卡片的四个字形（常规、粗体、斜体、粗斜体），已有的字形用它自己的预览条显示并给出字重、
 * 文件大小与 SHA-256，缺少的标为未上传；另给出家族名（效果 `fontFamily` 的取值）、来源、许可证与风格说明。只展示传入的数据，不发请求。
 */
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { FONT_SLOTS, previewUrl, type FontInfo } from "./api";
import { FontName } from "./FontName";
import { SLOT_NAMES, SOURCES, sizeText, weightText } from "./format";

/** `font` 为 null 时对话框关闭；关闭（Esc、遮罩或右上角）时调用 `onClose`。 */
export function FontDetails({ font, onClose }: { font: FontInfo | null; onClose: () => void }) {
  return (
    <Dialog open={font !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      {font && (
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{font.label}</DialogTitle>
            <DialogDescription>{font.style}</DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
            <dt className="text-muted-foreground">家族名</dt>
            <dd className="break-all font-mono">{font.family}</dd>
            <dt className="text-muted-foreground">来源</dt>
            <dd>{SOURCES[font.source]}{font.license ? ` · ${font.license}` : ""}</dd>
            <dt className="text-muted-foreground">大小</dt>
            <dd>{sizeText(font.size)}</dd>
          </dl>
          <ul aria-label="字形" className="grid gap-2 sm:grid-cols-2">
            {FONT_SLOTS.map((slot) => {
              const face = font.faces.find((item) => item.slot === slot);
              return (
                <li key={slot} aria-label={SLOT_NAMES[slot]} className={cn("min-w-0 rounded-md border p-3", !face && "border-dashed text-muted-foreground")}>
                  <p className="text-xs font-medium">{SLOT_NAMES[slot]}</p>
                  {face ? (
                    <>
                      <FontName label={font.label} url={previewUrl(face)} className="mt-2 h-8 w-full" />
                      <p className="mt-2 text-[11px] text-muted-foreground">字重 {weightText(face.weights)} · {sizeText(face.size)}</p>
                      {face.sha256 && <p className="truncate font-mono text-[11px] text-muted-foreground" title={face.sha256}>SHA-256 {face.sha256}</p>}
                    </>
                  ) : (
                    <p className="mt-2 text-xs">{slot === "regular" ? "未上传" : "未上传，渲染时由浏览器合成"}</p>
                  )}
                </li>
              );
            })}
          </ul>
          {font.source === "upload" && (
            <p className="text-xs leading-5 text-muted-foreground">同一家族的常规、粗体、斜体、粗斜体文件上传后自动归入这张卡片。</p>
          )}
        </DialogContent>
      )}
    </Dialog>
  );
}
