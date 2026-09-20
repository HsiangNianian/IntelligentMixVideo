/** 特效资产浏览与已添加对象列表；读取真实目录缩略图，选择操作更新父组件草稿和当前编辑对象。 */
import { useId, useState } from "react";
import { Check, Image, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { defaultEditor, textRoles, type Category, type Draft, type Editor, type EffectAsset, type TextRole } from "./model";
import { assetCategories, assetField } from "./effects";
import { trackLabel } from "./tracks";

/** 目录封面失败时展示明确占位，保留名称和选择入口。 */
function AssetCover({ asset }: { asset: EffectAsset }) {
  const [failed, setFailed] = useState(false);
  return asset.preview_url && !failed ? (
    <img src={asset.preview_url} alt="" loading="lazy" className="h-full w-full object-contain" onError={() => setFailed(true)} />
  ) : (
    <span className="flex flex-col items-center gap-1 text-muted-foreground">
      <Image className="size-6" aria-hidden="true" />
      <span className="text-[11px]">{failed ? "封面加载失败" : "暂无封面"}</span>
    </span>
  );
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
    <section aria-label="特效资产" className="flex min-w-0 flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-2"><h2 className="font-semibold">特效资产</h2><span className="text-xs text-muted-foreground">{options.length} 项</span></div>
      <div className="flex flex-wrap gap-1" aria-label="资产分类">
        {(Object.keys(assetCategories) as Category[]).map((value) => (
          <Button key={value} type="button" size="sm" variant={category === value ? "secondary" : "ghost"} aria-pressed={category === value} onClick={() => { setCategory(value); setLimit(24); }}>
            {assetCategories[value]}
          </Button>
        ))}
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-search`}>搜索特效</Label>
        <Input
          id={`${id}-search`}
          type="search"
          placeholder="名称或编号"
          value={search}
          onChange={(event) => { setSearch(event.target.value); setLimit(24); }}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
        />
      </div>
      {(category === "flower" || isMotion) && (
        <div className="space-y-2"><Label htmlFor={`${id}-target`}>应用到</Label><Select value={role} onValueChange={(value) => onTextTarget(value as TextRole)}><SelectTrigger id={`${id}-target`} className="w-full"><SelectValue /></SelectTrigger><SelectContent>
          {(Object.keys(textRoles) as TextRole[]).filter((value) => category !== "flower" || value !== "bubble").map((value) => <SelectItem key={value} value={value}>{textRoles[value]}</SelectItem>)}
        </SelectContent></Select></div>
      )}
      {(conflicting || needsBubble) && <p role="status" className="text-xs text-muted-foreground">{needsBubble ? "请先添加气泡样式。" : "循环动画与入场、出场动画互斥，请在右侧清除当前动画后选择。"}</p>}
      <div className="max-h-96 overflow-y-auto pr-1 @min-[1000px]:max-h-[65dvh]">
        <div className="grid grid-cols-2 gap-3">
          {options.slice(0, limit).map((asset) => {
            const selected = editor[selectedField] === asset.id;
            return <Button key={`${asset.id}-${asset.preview_url}`} type="button" variant="ghost" className={cn("h-auto min-w-0 flex-col items-stretch gap-2 whitespace-normal rounded-lg border p-2 text-left", selected && "border-primary bg-accent")} aria-label={`应用${assetCategories[category]}：${asset.name}`} aria-pressed={selected} disabled={conflicting || needsBubble} onClick={() => onAsset(asset, role)}>
              <span className="flex aspect-[4/3] items-center justify-center overflow-hidden rounded bg-muted"><AssetCover asset={asset} /></span>
              <span className="flex items-start justify-between gap-1"><span className="min-w-0 break-all text-xs">{asset.name}</span>{selected ? <Check className="mt-0.5 size-3.5" aria-hidden="true" /> : <Plus className="mt-0.5 size-3.5" aria-hidden="true" />}</span>
            </Button>;
          })}
        </div>
        {!options.length && <p role="status" className="py-8 text-center text-sm text-muted-foreground">{search.trim() ? "没有匹配的特效" : "此分类暂无特效"}</p>}
        {options.length > limit && <Button type="button" variant="outline" className="mt-3 w-full" onClick={() => setLimit((value) => value + 24)}>显示更多</Button>}
      </div>
    </section>
  );
}

/** 从保存字段派生对象列表；选中项只影响参数面板，不修改模板。 */
export function AppliedEffects({ draft, selected, onSelect }: {
  draft: Draft; selected: string | null; onSelect: (target: string) => void;
}) {
  return <section aria-label="已添加特效" className="space-y-3 border-t bg-card p-4">
    <h2 className="text-sm font-semibold">画面对象与已添加特效</h2>
    <p className="text-xs text-muted-foreground">重复添加效果会创建独立轨道；基础文字首次选择样式时沿用当前文字。动画应用到选中的文字轨道，参数面板可替换当前实例的样式。</p>
    <div className="flex flex-wrap gap-2">{draft.tracks.map((track) => <Button key={track.id} type="button" variant={selected === track.id ? "secondary" : "outline"} className="h-auto flex-col items-start" aria-pressed={selected === track.id} aria-label={`编辑${trackLabel(track, draft.tracks)}`} onClick={() => onSelect(track.id)}>
      <span>{trackLabel(track, draft.tracks)}</span>
      <span className="text-xs font-normal">{Number(track.start.toFixed(2))}{track.start_mode === "percent" ? "%" : " 秒"}开始 · {track.duration === null ? "持续到结束" : `持续 ${track.duration} 秒`}</span>
    </Button>)}</div>
    {!draft.tracks.length && <p className="text-sm text-muted-foreground">从左侧特效资产选择并添加效果。</p>}
  </section>;
}
