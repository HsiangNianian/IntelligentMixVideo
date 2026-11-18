/** 纸面画廊封面与卡片：显示已发布的预览 PNG，保留无效状态、编辑提示、打开、复制与删除操作。 */
import { useEffect, useState } from "react";
import { watchTemplateCover } from "@/features/templates/api";
import { apiBase } from "@/lib/api-base";
import { cn } from "@/lib/utils";
import { Icon } from "../icons";
import { IconButton, PulseDot, Skeleton } from "../ui";
import type { TemplateSummary } from "../types";

/** 各卡片共享已发布版本的图片数据，只负责自身图片地址的创建和释放。 */
export function TemplateCover({ template, compact, className }: { template: TemplateSummary; compact?: boolean; className?: string }) {
  const cover = template.cover;
  const address = apiBase();
  const [image, setImage] = useState("");
  useEffect(() => {
    if (!cover?.url) { setImage(""); return; }
    return watchTemplateCover(template.template_id, cover.publishedRevision, blob =>
      setImage(blob ? URL.createObjectURL(blob) : cover.url));
  }, [address, template.template_id, cover?.url, cover?.publishedRevision]);
  useEffect(() => () => { if (image.startsWith("blob:")) URL.revokeObjectURL(image); }, [image]);
  return <span aria-hidden="true" className={cn("relative flex aspect-[9/16] w-full items-center justify-center overflow-hidden bg-raised text-fg-subtle", className)}>
    {cover?.url && image ? <img src={image} alt="" className="size-full object-cover" /> : compact ? <Icon name="clapperboard-edit" className="size-4" /> : <span>暂无封面</span>}
  </span>;
}

interface CardProps {
  template: TemplateSummary;
  editing: boolean;
  dirty: boolean;
  onOpen: () => void;
  onCenter?: () => void;
  onDelete: () => void;
  onCopyId: () => void;
}

/** 卡片只负责打开与管理；空间位置由画廊决定，无效模板仍可删除。 */
export function TemplateCard({ template, editing, dirty, onOpen, onCenter, onDelete, onCopyId }: CardProps) {
  const invalid = Boolean(template.validation_error);
  const needsCover = !invalid && (!template.cover?.url || template.cover.revision !== template.cover.publishedRevision);
  return <article className="group relative [container-type:inline-size]">
    <button type="button" data-template-id={template.template_id} disabled={invalid && !onCenter} onClick={onCenter ?? onOpen} aria-label={`${onCenter ? "居中展示" : "选择模板"}：${template.name}`}
      className={cn("block w-full overflow-hidden rounded-[24px] border border-line-strong text-left shadow-lift focus-visible:outline-offset-4", editing && "ring-1 ring-brand ring-offset-4 ring-offset-canvas")}>
      {invalid ? <span className="flex aspect-[9/16] flex-col items-center justify-center gap-3 bg-raised px-5 text-center text-warning"><Icon name="danger-triangle" className="size-6" /><span className="font-serif text-xl">{template.name}</span><span className="text-xs">{template.validation_error}</span></span> : <TemplateCover template={template} />}
    </button>
    {needsCover && <span className="pointer-events-none absolute inset-x-2 bottom-2 rounded-md bg-raised/95 p-1.5 text-center text-xs">封面更新失败</span>}
    {editing && <span className="pointer-events-none absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-1 text-[10px] text-white">{dirty && <PulseDot className="text-warning" />}{dirty ? "未保存" : "编辑中"}</span>}
    <div data-gallery-actions className={cn("absolute right-2 top-2 flex flex-col gap-1 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100", !invalid && "opacity-0")}>
      {!invalid && onCenter && <IconButton label="打开编辑" variant="glass" onClick={onOpen}><Icon name="arrow-right-up" /></IconButton>}
      {!invalid && <IconButton label="复制模板 ID" variant="glass" onClick={onCopyId}><Icon name="copy" /></IconButton>}
      <IconButton label={`删除模板：${template.name}`} variant="glass-danger" onClick={onDelete}><Icon name="trash-bin-minimalistic" /></IconButton>
    </div>
  </article>;
}

/** 加载占位保留竖屏封面比例。 */
export function TemplateCardSkeleton() {
  return <Skeleton aria-hidden="true" className="aspect-[9/16] w-full rounded-[24px]" />;
}
