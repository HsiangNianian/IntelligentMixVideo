/** 模板编辑中的 Remotion 资产：左侧按分类从发布目录添加片段，右侧列出模板里的片段、所占的模板对象，并可选中调整参数。 */
import { useState } from "react";
import { Plus, RefreshCw, Trash2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import type { SpriteAsset } from "@/features/sprites/model";
import { CATEGORIES, categoryOf, type CategoryId } from "@/features/sprites/parameters";
import type { Environment } from "./api";
import { textRoles, type RemotionClip } from "./model";
import { clipSlot } from "./clips";
import { effectTargets } from "./effects";
import type { useSpriteCatalog } from "./useSpriteCatalog";

type Catalog = ReturnType<typeof useSpriteCatalog>;

/** 左侧资产区：展开时读取目录，按字效、滤镜、转场与视频特效分类列出；本地模板不支持 Remotion 资产，只说明原因。 */
export function SpriteCatalog({ environment, catalog, onAdd }: { environment: Environment; catalog: Catalog; onAdd: (asset: SpriteAsset) => void }) {
  const [open, setOpen] = useState(false);
  // 未手动选择时打开第一个有资产的分类，新保存的资产不会藏在空分类后面。
  const [chosen, setChosen] = useState<CategoryId | null>(null);
  const usable = environment === "cloud";
  const category = chosen ?? CATEGORIES.find((item) => catalog.assets?.some((asset) => categoryOf(asset.kind) === item.id))?.id ?? "text";
  const shown = catalog.assets?.filter((asset) => categoryOf(asset.kind) === category);
  return (
    <section aria-label="Remotion 资产" className="space-y-2 border-t p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold"><Wand2 className="size-3.5" aria-hidden="true" />Remotion 资产</h2>
        <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]" aria-expanded={open} onClick={() => { const next = !open; setOpen(next); if (next) catalog.refresh(); }}>{open ? "收起" : "展开"}</Button>
      </div>
      {environment !== "cloud" && <p role="status" className="text-[11px] text-muted-foreground">Remotion 资产只能添加到云端模板。</p>}
      {open && environment === "cloud" && <>
        <p className="text-[11px] text-muted-foreground">资产画面以透明层叠加在预览上。字效作用于当前选择的标题或字幕，转场占用空闲的视频交界处，视频特效与画面特效共用一个名额。</p>
        {catalog.error && <p role="alert" className="text-[11px] text-destructive">{catalog.error}</p>}
        {catalog.assets && !catalog.assets.length && <p role="status" className="text-[11px] text-muted-foreground">还没有资产，请在字效工作区的成功版本上选择「保存到资产」。</p>}
        {!!catalog.assets?.length && <ToggleGroup type="single" spacing={1} value={category} onValueChange={(value) => { if (value) setChosen(value as CategoryId); }} aria-label="Remotion 资产分类" className="w-full flex-wrap">
          {CATEGORIES.map((item) => (
            <ToggleGroupItem key={item.id} value={item.id} className="template-category-pill h-6 gap-1 rounded-full border px-2 text-[11px] font-normal">
              {item.label} <span aria-hidden="true" className="template-category-count">{catalog.assets!.filter((asset) => categoryOf(asset.kind) === item.id).length}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>}
        {shown && catalog.assets!.length > 0 && !shown.length && <p role="status" className="text-[11px] text-muted-foreground">这个分类还没有资产。</p>}
        <ul className="space-y-1">
          {shown?.map((asset) => (
            <li key={asset.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
              <span className="min-w-0"><span className="block truncate text-[11px] font-medium">{asset.name}</span><span className="block text-[10px] tabular-nums text-muted-foreground">{Number(asset.seconds.toFixed(1))} 秒 · {asset.width}×{asset.height} @ {asset.fps} FPS</span></span>
              <Button type="button" variant="outline" size="sm" className="h-6 gap-1 px-2 text-[11px]" disabled={!usable} aria-label={`添加 Remotion 资产：${asset.name}`} onClick={() => onAdd(asset)}><Plus className="size-3" aria-hidden="true" />添加</Button>
            </li>
          ))}
        </ul>
        <Button type="button" variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[11px]" onClick={catalog.refresh}><RefreshCw className="size-3" aria-hidden="true" />刷新目录</Button>
      </>}
    </section>
  );
}

/** 右侧片段列表：显示名称、时间与是否改过参数，点选打开参数设置，可移除；本地模板不显示。资产已不可用时提示先移除，否则服务端会拒绝保存。 */
export function SpriteClips({ environment, clips, catalog, duration, selected, onSelect, onRemove }: {
  environment: Environment; clips: RemotionClip[]; catalog: Catalog; duration: number | undefined;
  selected: string | null; onSelect: (id: string) => void; onRemove: (id: string) => void;
}) {
  if (environment !== "cloud") return null;
  const { assets } = catalog;
  return (
    <section aria-label="已添加 Remotion 资产" className="space-y-2 border-b p-4">
      <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">Remotion 资产</h2><span className="text-[11px] tabular-nums text-muted-foreground">{clips.length}</span></div>
      {!clips.length && <p className="text-[11px] text-muted-foreground">从左侧 Remotion 资产添加，点选后在右侧调整时间、参数与位置。</p>}
      {catalog.error && <p role="alert" className="text-[11px] text-destructive">{catalog.error}</p>}
      <ul className="space-y-1">
        {clips.map((clip) => {
          const asset = assets?.find((item) => item.id === clip.sprite_id);
          const name = asset?.name ?? (assets ? "未找到的资产" : "读取中…");
          const end = duration === undefined ? undefined : Math.min(duration, clip.start + (clip.duration ?? asset?.seconds ?? 0));
          const timing = end === undefined ? "等待预览时间" : end <= clip.start ? "当前视频未应用" : `${Number(clip.start.toFixed(1))}～${Number(end.toFixed(1))} 秒`;
          const edited = Object.keys(clip.parameters).length + Object.keys(clip.transform).length > 0;
          const slot = clipSlot(clip, asset);
          const place = slot === "title" || slot === "subtitle" ? `${textRoles[slot]}字效` : slot === "transition" ? `镜头转场 ${clip.transition_boundary ?? ""}`.trim() : slot ? effectTargets[slot] : asset ? "未选择作用对象" : "";
          return <li key={clip.id} aria-label={`Remotion 资产：${name}`} className={cn("flex items-center justify-between gap-2 rounded-md border px-2 py-1.5", selected === clip.id && "border-primary ring-1 ring-primary/20")}>
            <button type="button" className="min-w-0 flex-1 text-left" aria-pressed={selected === clip.id} aria-label={`设置 Remotion 资产：${name}`} onClick={() => onSelect(clip.id)}><span className="block truncate text-[12px] font-medium">{name}</span><span className="block text-[11px] tabular-nums text-muted-foreground">{place && `${place} · `}{timing}{edited && " · 已调整参数"}{assets && !asset && " · 资产已不可用，保存前请移除"}</span></button>
            <Button type="button" variant="ghost" size="icon" className="size-6" aria-label={`移除 Remotion 资产：${name}`} onClick={() => onRemove(clip.id)}><Trash2 className="size-3.5" aria-hidden="true" /></Button>
          </li>;
        })}
      </ul>
    </section>
  );
}
