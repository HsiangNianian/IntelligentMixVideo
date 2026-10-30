/** 特效资产浏览与已添加对象列表；读取真实目录缩略图，选择操作更新父组件草稿和当前编辑对象。 */
import { useId, useState } from "react";
import { Captions, Circle, Heading1, Layers, MessageCircle, Shuffle, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { defaultEditor, textRoles, type Category, type Draft, type Editor, type EffectAsset, type EffectTrack, type TextRole } from "./model";
import { assetCategories, assetField, isSelectableAsset, type EffectTarget } from "./effects";
import { trackLabel, trackLayers } from "./tracks";
import { FRAME_MASK_OBJECT_ID } from "./frameMask";
import { AssetBrowser, CoverPlaceholder } from "./AssetBrowser";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/** 目录封面失败时展示明确占位，保留名称和选择入口。 */
function AssetCover({ asset }: { asset: EffectAsset }) {
  const [failed, setFailed] = useState(false);
  return asset.preview_url && !failed ? (
    <img src={asset.preview_url} alt="" loading="lazy" className="h-full w-full object-contain" onError={() => setFailed(true)} />
  ) : (
    <CoverPlaceholder text={failed ? "封面加载失败" : "暂无封面"} />
  );
}

/** 画面对象使用独立图标和颜色，列表与设置栏共享。 */
export const objectIcons: Record<EffectTarget | "frame-mask" | "avatar-mask", LucideIcon> = { title: Heading1, subtitle: Captions, bubble: MessageCircle, filter: SlidersHorizontal, vfx: Sparkles, transition: Shuffle, "frame-mask": Layers, "avatar-mask": Circle };

/** 对象列表和设置栏展示带类别色块的图标。 */
export function ObjectIcon({ target }: { target: keyof typeof objectIcons }) {
  const Icon = objectIcons[target];
  return <span data-object={target} className="template-object-icon flex size-7 shrink-0 items-center justify-center rounded-md" aria-hidden="true"><Icon className="size-3.5" /></span>;
}

/** 按分类和名称浏览目录；文字资产显式选择作用对象，目录数量较多时分批展示。 */
export function EffectAssets({ editor = defaultEditor, catalog, textTarget, onTextTarget, onAsset, textEditor, textTracks, selectedText, onSelectText }: {
  editor?: Editor;
  catalog: EffectAsset[];
  textTarget: TextRole;
  onTextTarget: (target: TextRole) => void;
  onAsset: (asset: EffectAsset, target: TextRole) => void;
  textEditor?: Editor;
  textTracks?: EffectTrack[];
  selectedText?: string | null;
  onSelectText?: (id: string) => void;
}) {
  const id = useId();
  const [category, setCategory] = useState<Category>("flower");
  const selectableAssets = catalog.filter(isSelectableAsset);
  const isMotion = category === "in" || category === "out" || category === "loop";
  const role = category === "flower" && textTarget === "bubble" ? "title" : textTarget;
  editor = (isMotion || category === "flower") && textEditor ? textEditor : editor;
  const conflicting = isMotion && (category === "loop" ? Boolean(editor[`${role}In`] || editor[`${role}Out`]) : Boolean(editor[`${role}Loop`]));
  const needsBubble = isMotion && role === "bubble" && !editor.bubble;
  const selectedField = assetField(category, role);
  const options = textTracks?.filter((track) => track.target === "title" || track.target === "subtitle" || (isMotion && track.target === "bubble"));
  return (
    <section aria-label="特效资产" className="flex min-w-0 flex-col gap-2.5 p-3">
      <div className="flex items-center justify-between gap-2"><h2 className="text-[13px] font-semibold">特效资产</h2><span className="rounded-md bg-muted px-1.5 py-1 text-[11px] text-muted-foreground">{selectableAssets.length} 项</span></div>
      <AssetBrowser
        categories={(Object.keys(assetCategories) as Category[]).map((value) => ({ value, label: assetCategories[value], count: selectableAssets.filter((asset) => asset.category === value).length }))}
        category={category}
        onCategory={(value) => setCategory(value as Category)}
        categoriesLabel="资产分类"
        searchLabel="搜索特效"
        searchPlaceholder="名称或编号"
        noun="特效"
        items={selectableAssets.filter((asset) => asset.category === category)}
        searchText={(asset) => `${asset.name} ${asset.effect_id}`}
        card={(asset) => ({
          id: `${asset.id}-${asset.preview_url}`,
          name: asset.name,
          label: `应用${assetCategories[category]}：${asset.name}`,
          cover: <AssetCover asset={asset} />,
          selected: editor[selectedField] === asset.id,
          disabled: conflicting || needsBubble,
          onSelect: () => onAsset(asset, role),
        })}
        controls={<>
          {(category === "flower" || isMotion) && (
            <div className="space-y-1.5"><Label htmlFor={`${id}-target`} className="text-[11px]">应用到</Label><Select value={options ? options.find((track) => track.id === selectedText)?.id ?? options.find((track) => track.target === role)?.id : role} onValueChange={(value) => options ? onSelectText?.(value) : onTextTarget(value as TextRole)}><SelectTrigger id={`${id}-target`} size="sm" className="w-full px-2.5 text-[11px]"><SelectValue /></SelectTrigger><SelectContent>
              {options ? options.map((track) => <SelectItem key={track.id} value={track.id} className="text-xs">{trackLabel(track, textTracks!)}</SelectItem>) : (Object.keys(textRoles) as TextRole[]).filter((value) => category !== "flower" || value !== "bubble").map((value) => <SelectItem key={value} value={value} className="text-xs">{textRoles[value]}</SelectItem>)}
            </SelectContent></Select></div>
          )}
          {(conflicting || needsBubble) && <p role="status" className="text-[11px] text-muted-foreground">{needsBubble ? "请先添加气泡样式。" : "循环动画与入场、出场动画互斥，请在右侧清除当前动画后选择。"}</p>}
        </>}
      />
    </section>
  );
}

/** 从保存字段派生对象列表；蒙版也作为画面对象展示，继续使用模板级配置保存。 */
export function AppliedEffects({ draft, catalog, resolved, selected, onSelect }: {
  draft: Draft; catalog: EffectAsset[]; resolved?: import("./tracks").ResolvedTimeline; selected: string | null; onSelect: (target: string) => void;
}) {
  const objects: { id: string; target: keyof typeof objectIcons; label: string; detail: string }[] = [...draft.tracks]
    .sort((a, b) => trackLayers[a.target] - trackLayers[b.target] || (a.target === "transition" && b.target === "transition" ? a.transition_boundary! - b.transition_boundary! : 0))
    .map((track) => {
      const effectId = { title: track.editor.titleFlower, subtitle: track.editor.subtitleFlower, bubble: track.editor.bubble, filter: track.editor.filter, vfx: track.editor.vfx, transition: track.editor.transition }[track.target];
      const effect = catalog.find((item) => item.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : "未设置效果");
      const timing = resolved?.tracks[track.id];
      const interval = timing ? timing.end > timing.start ? `${Number(timing.start.toFixed(1))}～${Number(timing.end.toFixed(1))} 秒` : "当前视频未应用" : resolved ? "当前视频未应用" : "等待预览时间";
      return { id: track.id, target: track.target, label: trackLabel(track, draft.tracks), detail: `${effect} · ${interval}` };
    });
  if (draft.frame_mask) objects.push({ id: FRAME_MASK_OBJECT_ID, target: "frame-mask", label: "上下蒙版", detail: "上下背景栏 · 贯穿全片" });
  if (draft.avatar_effects?.openingEnabled) objects.push({ id: "avatar-mask", target: "avatar-mask", label: "圆形蒙版", detail: `开场显示 · ${draft.avatar_effects.openingDuration} 秒` });
  return <section aria-label="已添加特效" className="space-y-3 border-b p-4">
    <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">画面对象</h2><span className="text-[11px] tabular-nums text-muted-foreground">{objects.length}</span></div>
    <div className="space-y-0.5">{objects.map((object) => <Button key={object.id} type="button" variant="ghost" data-object={object.target} className="template-object-row h-auto w-full min-w-0 justify-start gap-2.5 rounded-md px-2 py-2 text-left" aria-pressed={selected === object.id} aria-label={object.target === "frame-mask" ? "编辑上下蒙版对象" : `编辑${object.label}`} onClick={() => onSelect(object.id)}>
      <ObjectIcon target={object.target} /><span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium leading-5">{object.label}</span><span className="block truncate text-[11px] leading-4 tabular-nums text-muted-foreground">{object.detail}</span></span>
    </Button>)}</div>
    {!objects.length && <Empty className="border p-5 md:p-5"><EmptyHeader><EmptyMedia variant="icon"><Layers className="size-5" aria-hidden="true" /></EmptyMedia><EmptyTitle className="text-sm">还没有画面对象</EmptyTitle><EmptyDescription className="text-xs">从左侧特效资产选择并添加效果。</EmptyDescription></EmptyHeader></Empty>}
  </section>;
}
