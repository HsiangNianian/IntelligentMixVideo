/** 特效资产浏览与已添加对象列表；读取真实目录缩略图，选择操作更新父组件草稿和当前编辑对象。 */
import { useId, useState } from "react";
import { Captions, Check, Heading1, Image, Layers, MessageCircle, Plus, SearchX, Shuffle, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { defaultEditor, textRoles, type Category, type Draft, type Editor, type EffectAsset, type TextRole } from "./model";
import { assetCategories, assetField, type EffectTarget } from "./effects";
import { trackLabel } from "./tracks";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/** 目录封面失败时展示明确占位，保留名称和选择入口。 */
function AssetCover({ asset }: { asset: EffectAsset }) {
  const [failed, setFailed] = useState(false);
  return asset.preview_url && !failed ? (
    <img src={asset.preview_url} alt="" loading="lazy" className="h-full w-full object-contain" onError={() => setFailed(true)} />
  ) : (
    <span className="flex flex-col items-center gap-1 text-muted-foreground">
      <Image className="size-5" aria-hidden="true" />
      <span className="text-[11px]">{failed ? "封面加载失败" : "暂无封面"}</span>
    </span>
  );
}

/** 六类画面对象使用独立图标和颜色，列表与设置栏共享。 */
export const objectIcons: Record<EffectTarget, LucideIcon> = { title: Heading1, subtitle: Captions, bubble: MessageCircle, filter: SlidersHorizontal, vfx: Sparkles, transition: Shuffle };

/** 对象列表和设置栏展示带类别色块的图标。 */
export function ObjectIcon({ target }: { target: EffectTarget }) {
  const Icon = objectIcons[target];
  return <span data-object={target} className="template-object-icon flex size-7 shrink-0 items-center justify-center rounded-md" aria-hidden="true"><Icon className="size-3.5" /></span>;
}

/** 按分类和名称浏览目录；文字资产显式选择作用对象，目录数量较多时分批展示。 */
export function EffectAssets({ editor = defaultEditor, catalog, textTarget, onTextTarget, onAsset, textEditor }: {
  editor?: Editor;
  catalog: EffectAsset[];
  textTarget: TextRole;
  onTextTarget: (target: TextRole) => void;
  onAsset: (asset: EffectAsset, target: TextRole) => void;
  textEditor?: Editor;
}) {
  const id = useId();
  const [category, setCategory] = useState<Category>("flower");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(24);
  const isMotion = category === "in" || category === "out" || category === "loop";
  const role = category === "flower" && textTarget === "bubble" ? "title" : textTarget;
  editor = (isMotion || category === "flower") && textEditor ? textEditor : editor;
  const options = catalog.filter((asset) => asset.category === category && `${asset.name} ${asset.effect_id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const conflicting = isMotion && (category === "loop" ? Boolean(editor[`${role}In`] || editor[`${role}Out`]) : Boolean(editor[`${role}Loop`]));
  const needsBubble = isMotion && role === "bubble" && !editor.bubble;
  const selectedField = assetField(category, role);
  return (
    <section aria-label="特效资产" className="flex min-w-0 flex-col gap-2.5 p-3">
      <div className="flex items-center justify-between gap-2"><h2 className="text-[13px] font-semibold">特效资产</h2><span className="rounded-md bg-muted px-1.5 py-1 text-[11px] text-muted-foreground">{catalog.length} 项</span></div>
      {/* 分类为单选切换组：再次点击当前分类不会取消选择。 */}
      <ToggleGroup type="single" spacing={1} value={category} onValueChange={(value) => { if (value) { setCategory(value as Category); setLimit(24); } }} aria-label="资产分类" className="template-asset-categories w-full flex-wrap">
        {(Object.keys(assetCategories) as Category[]).map((value) => (
          <ToggleGroupItem key={value} value={value} className="template-category-pill h-6 gap-1 rounded-full border px-2 text-[11px] font-normal">
            {assetCategories[value]} <span aria-hidden="true" className="template-category-count">{catalog.filter((asset) => asset.category === value).length}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div className="space-y-2">
        <Label htmlFor={`${id}-search`} className="sr-only">搜索特效</Label>
        <Input
          id={`${id}-search`}
          type="search"
          placeholder="名称或编号"
          value={search}
          onChange={(event) => { setSearch(event.target.value); setLimit(24); }}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
          className="h-8 px-2.5 text-[11px] md:text-[11px]"
        />
      </div>
      {(category === "flower" || isMotion) && (
        <div className="space-y-1.5"><Label htmlFor={`${id}-target`} className="text-[11px]">应用到</Label><Select value={role} onValueChange={(value) => onTextTarget(value as TextRole)}><SelectTrigger id={`${id}-target`} size="sm" className="w-full px-2.5 text-[11px]"><SelectValue /></SelectTrigger><SelectContent>
          {(Object.keys(textRoles) as TextRole[]).filter((value) => category !== "flower" || value !== "bubble").map((value) => <SelectItem key={value} value={value} className="text-xs">{textRoles[value]}</SelectItem>)}
        </SelectContent></Select></div>
      )}
      {(conflicting || needsBubble) && <p role="status" className="text-[11px] text-muted-foreground">{needsBubble ? "请先添加气泡样式。" : "循环动画与入场、出场动画互斥，请在右侧清除当前动画后选择。"}</p>}
      <div className="max-h-96 overflow-y-auto pr-1 @min-[1000px]:max-h-[65dvh]">
        <div className="grid grid-cols-2 gap-2">
          {options.slice(0, limit).map((asset) => {
            const selected = editor[selectedField] === asset.id;
            return <Button key={`${asset.id}-${asset.preview_url}`} type="button" variant="ghost" className={cn("template-asset-card h-auto min-w-0 flex-col items-stretch gap-1 whitespace-normal rounded-lg border p-1 text-left", selected && "is-selected")} aria-label={`应用${assetCategories[category]}：${asset.name}`} aria-pressed={selected} disabled={conflicting || needsBubble} onClick={() => onAsset(asset, role)}>
              <span className="flex h-[68px] items-center justify-center overflow-hidden rounded bg-muted"><AssetCover asset={asset} /></span>
              <span className="flex min-w-0 items-start justify-between gap-0.5"><span className="min-w-0 break-all text-[11px] leading-4">{asset.name}</span>{selected ? <Check className="size-3 shrink-0" aria-hidden="true" /> : <Plus className="size-3 shrink-0" aria-hidden="true" />}</span>
            </Button>;
          })}
        </div>
        {!options.length && <Empty className="p-6 md:p-6"><EmptyHeader><EmptyMedia variant="icon"><SearchX className="size-5" aria-hidden="true" /></EmptyMedia><EmptyDescription role="status" className="text-xs">{search.trim() ? "没有匹配的特效" : "此分类暂无特效"}</EmptyDescription></EmptyHeader></Empty>}
        {options.length > limit && <Button type="button" variant="outline" className="mt-2 h-8 w-full text-[11px]" onClick={() => setLimit((value) => value + 24)}>显示更多</Button>}
      </div>
    </section>
  );
}

/** 从保存字段派生对象列表；每行显示类型、效果和当前预览区间。 */
export function AppliedEffects({ draft, catalog, duration, selected, onSelect }: {
  draft: Draft; catalog: EffectAsset[]; duration: number; selected: string | null; onSelect: (target: string) => void;
}) {
  return <section aria-label="已添加特效" className="space-y-3 border-b p-4">
    <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">画面对象</h2><span className="text-[11px] tabular-nums text-muted-foreground">{draft.tracks.length}</span></div>
    <div className="space-y-0.5">{draft.tracks.map((track) => {
      const effectId = { title: track.editor.titleFlower, subtitle: track.editor.subtitleFlower, bubble: track.editor.bubble, filter: track.editor.filter, vfx: track.editor.vfx, transition: track.editor.transition }[track.target];
      const effect = catalog.find((item) => item.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : "未设置效果");
      const start = track.start_mode === "percent" ? track.start * duration / 100 : track.start;
      const end = Math.min(duration, track.duration === null ? duration : start + track.duration);
      return <Button key={track.id} type="button" variant="ghost" data-object={track.target} className="template-object-row h-auto w-full min-w-0 justify-start gap-2.5 rounded-md px-2 py-2 text-left" aria-pressed={selected === track.id} aria-label={`编辑${trackLabel(track, draft.tracks)}`} onClick={() => onSelect(track.id)}>
        <ObjectIcon target={track.target} /><span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium leading-5">{trackLabel(track, draft.tracks)}</span><span className="block truncate text-[11px] leading-4 tabular-nums text-muted-foreground">{effect} · {Number(start.toFixed(1))}～{Number(end.toFixed(1))} 秒</span></span>
      </Button>;
    })}</div>
    {!draft.tracks.length && <Empty className="border p-5 md:p-5"><EmptyHeader><EmptyMedia variant="icon"><Layers className="size-5" aria-hidden="true" /></EmptyMedia><EmptyTitle className="text-sm">还没有画面对象</EmptyTitle><EmptyDescription className="text-xs">从左侧特效资产选择并添加效果。</EmptyDescription></EmptyHeader></Empty>}
  </section>;
}
