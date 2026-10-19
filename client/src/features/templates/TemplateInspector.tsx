/** 模版编辑右栏：按所选对象切换外观、时间与关键词设置，复用原有参数控件。 */
import { useEffect, useId, useState } from "react";
import { ListOrdered, Plus, Shuffle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { isKeywordTarget, textRole, type Draft, type EffectAsset, type EffectDraft, type EffectTrack } from "./model";
import type { SpriteAsset } from "@/features/sprites/model";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import { transitionItems } from "./transitions";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { effectTargets, trackEffect } from "./effects";
import { EffectEditor } from "./EffectEditor";
import { ObjectIcon } from "./EffectAssets";
import { TrackTiming } from "./TrackTiming";
import { EffectCombobox } from "./EffectCombobox";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { trackLabel, type ResolvedTrack } from "./tracks";

/** 全部转场共用选择方式，IMS 共用持续时间，每个条目独立选择效果。 */
export function TransitionInspector({ draft, catalog, sprites, onChoose, onRemove, onDuration, onMode, onEnabled, onSelect }: {
  draft: Draft; catalog: EffectAsset[]; sprites: SpriteAsset[];
  onChoose: (id: string, value: string) => void;
  onRemove: (id: string) => void;
  onDuration: (seconds: number) => void; onMode: (mode: "sequence" | "random") => void;
  onEnabled: (enabled: boolean) => void;
  onSelect: (id: string) => void;
}) {
  const id = useId();
  const [length, setLength] = useState(String(draft.transition_duration_seconds));
  useEffect(() => setLength(String(draft.transition_duration_seconds)), [draft.transition_duration_seconds]);
  const items = transitionItems(draft);
  const options = [...catalog.filter((asset) => asset.category === "transition/normal").map((asset) => ({ value: asset.id, label: asset.name })),
    ...sprites.filter((asset) => asset.kind === SpriteKind.TRANSITION_OVERLAY).map((asset) => ({ value: `sprite:${asset.id}`, label: `Remotion · ${asset.name}` }))];
  return <section aria-label="转场设置" className="template-inspector-fields min-w-0 space-y-3 p-4">
    <div className="flex items-center justify-between gap-2">
      <h2 className="text-[13px] font-semibold leading-5">镜头转场</h2>
      <Switch aria-label="使用转场" checked={draft.transition_enabled !== false} onCheckedChange={onEnabled} />
    </div>
    <div className="space-y-2">
      <Label className="text-xs text-muted-foreground">转场选择方式</Label>
      <ToggleGroup type="single" spacing={1} value={draft.transition_mode ?? "sequence"} aria-label="转场选择方式"
        className="grid w-full grid-cols-2 gap-1 rounded-lg border border-border/60 bg-muted/60 p-1"
        onValueChange={(value) => { if (value === "sequence" || value === "random") onMode(value); }}>
        {([["sequence", "顺序选择", ListOrdered], ["random", "随机选择", Shuffle]] as const).map(([value, label, Icon]) => (
          <ToggleGroupItem key={value} value={value}
            className="h-8 gap-1.5 rounded-md px-2 text-xs text-muted-foreground transition-[color,background-color,box-shadow] hover:bg-background/50 hover:text-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow-sm">
            <Icon className="size-3.5" aria-hidden="true" />{label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
    <div aria-label="转场时间设置" className="space-y-1.5">
      <Label htmlFor={id}>IMS 转场持续时间 / 秒</Label>
      <div className="flex items-center gap-3">
        <Slider label="转场持续时间滑块" min={0.1} max={3} step={0.1} value={[draft.transition_duration_seconds]}
          onValueChange={([next]) => { setLength(String(next)); onDuration(next); }} className="flex-1" />
        <Input id={id} type="number" min={0.1} max={3} step="any" required value={length} className="w-[4.5rem] shrink-0 text-right"
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
          onChange={(event) => {
            const value = event.target.value;
            const seconds = value.trim() ? Number(value) : NaN;
            const next = Number.isFinite(seconds) ? Math.min(3, Math.max(0.1, seconds)) : NaN;
            setLength(Number.isFinite(next) && next !== seconds ? String(next) : value);
            onDuration(next);
          }} />
      </div>
    </div>
    {items.map((item, index) => <div key={item.id} className="space-y-2" aria-label={`转场 ${index + 1}`}>
      <Label>转场 {index + 1}</Label>
      <div className="flex items-center gap-1.5">
        <div className="min-w-0 flex-1"><EffectCombobox value={"editor" in item ? item.editor.transition : `sprite:${item.sprite_id}`} ariaLabel={`转场 ${index + 1} 效果`} placeholder="选择转场效果" searchPlaceholder="搜索转场"
          options={options} onChange={(value) => onChoose(item.id, value)} /></div>
        <Hint label={`删除转场 ${index + 1}`}><Button type="button" variant="ghost" size="icon" className="size-7 shrink-0 text-muted-foreground hover:text-destructive" aria-label={`删除转场 ${index + 1}`} onClick={() => onRemove(item.id)}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
      </div>
      {"sprite_id" in item && <Button type="button" size="sm" variant="outline" onClick={() => onSelect(item.id)}>设置转场参数</Button>}
    </div>)}
    <EffectCombobox value="" ariaLabel="添加转场" placeholder="添加转场" searchPlaceholder="搜索要添加的转场" options={options}
      disabled={draft.tracks.length + draft.clips.length >= 100} onChange={(value) => onChoose("", value)}
      className="flex h-8 items-center justify-center gap-1.5 rounded-md border border-dashed border-input text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50">
      <Plus className="size-3.5" aria-hidden="true" />添加转场
    </EffectCombobox>
    {!items.length && <p className="text-xs text-muted-foreground">尚未添加转场，三个视频连续播放。</p>}
  </section>;
}

export type InspectorTab = "appearance" | "timing" | "keyword";

/** 字幕只提供外观与关键词页签，关键词对象只有外观（时间跟随所属文字）；其他对象的时间输入保持挂载以便保存前校验。 */
export function TemplateInspector({ track, draft, catalog, duration, resolved, tab, onTabChange, onTimingChange, onEffectChange, onRemove, onClose }: {
  track: EffectTrack;
  draft: EffectDraft;
  catalog: EffectAsset[];
  duration: number;
  resolved?: ResolvedTrack;
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  onTimingChange: (timing: Pick<EffectTrack, "start_mode" | "start" | "duration">) => void;
  onEffectChange: (draft: EffectDraft) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const id = useId();
  const hasKeywords = track.target === "title" || track.target === "subtitle";
  const keywordObject = isKeywordTarget(track.target);
  const tabs: InspectorTab[] = keywordObject ? ["appearance"] : track.target === "subtitle" || track.language === "en" ? ["appearance", "keyword"] : hasKeywords ? ["appearance", "timing", "keyword"] : ["appearance", "timing"];
  const activeTab = tabs.includes(tab) ? tab : tabs[0];
  const effectId = trackEffect(track);
  const effect = catalog.find((asset) => asset.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : "未设置效果");
  return <section aria-label="画面对象设置" className="min-w-0">
    {track.language === "en" && <p className="px-4 pt-3 text-[11px] text-muted-foreground">成片中英文过长时自动缩小字号，尽量保持单行；预览暂不支持自动缩小。</p>}
    {keywordObject && <p className="px-4 pt-3 text-[11px] text-muted-foreground">单独显示所属{textRole(track.target) === "title" ? "标题" : "字幕"}的关键词，时间跟随它：预览取示例文字中的关键词，合成时换成切片给出的关键词，没有关键词的片段不显示。</p>}
    <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3">
      <div className="flex min-w-0 items-center gap-2.5"><ObjectIcon target={track.target} /><div className="min-w-0"><h2 className="text-[13px] font-semibold leading-5">{trackLabel(track, [track])}</h2><p className="mt-0.5 truncate text-[11px] leading-3 text-muted-foreground">{effect}</p></div></div>
      <Hint label="关闭特效设置"><Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label="关闭特效设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    <div role="tablist" aria-label={`${effectTargets[track.target]}设置分类`} className={`grid border-b px-4 ${tabs.length === 1 ? "grid-cols-1" : tabs.length === 3 ? "grid-cols-3" : "grid-cols-2"}`}>{tabs.map((value) => <button key={value} type="button" role="tab" id={`${id}-${value}`} aria-controls={`${id}-panel-${value}`} aria-selected={activeTab === value} onClick={() => onTabChange(value)} className="border-b-2 border-transparent px-1 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring data-[selected=true]:border-primary data-[selected=true]:text-primary" data-selected={activeTab === value}>{value === "appearance" ? "外观与效果" : value === "timing" ? "时间设置" : "关键词设置"}</button>)}</div>
    <div id={`${id}-panel-appearance`} role="tabpanel" aria-labelledby={`${id}-appearance`} hidden={activeTab !== "appearance"}><EffectEditor draft={draft} catalog={catalog} onChange={onEffectChange} target={track.target} language={track.language} onRemove={onRemove} onClose={onClose} view="appearance" showHeader={false} /></div>
    {tabs.includes("timing") && <div id={`${id}-panel-timing`} role="tabpanel" aria-labelledby={`${id}-timing`} hidden={activeTab !== "timing"}><TrackTiming track={track} duration={duration} resolved={resolved} onChange={onTimingChange} /></div>}
    {hasKeywords && <div id={`${id}-panel-keyword`} role="tabpanel" aria-labelledby={`${id}-keyword`} hidden={activeTab !== "keyword"}><EffectEditor draft={draft} catalog={catalog} onChange={onEffectChange} target={track.target} language={track.language} onRemove={onRemove} onClose={onClose} view="keyword" showHeader={false} /></div>}
  </section>;
}
