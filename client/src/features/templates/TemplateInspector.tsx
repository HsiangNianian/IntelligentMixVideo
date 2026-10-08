/** 模版编辑右栏：按所选对象切换外观、时间与关键词设置，复用原有参数控件。 */
import { useId } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import type { EffectAsset, EffectDraft, EffectTrack } from "./model";
import { effectTargets } from "./effects";
import { EffectEditor } from "./EffectEditor";
import { ObjectIcon } from "./EffectAssets";
import { TrackTiming } from "./TrackTiming";

export type InspectorTab = "appearance" | "timing" | "keyword";

/** 页签只控制可见区域，时间输入保持挂载以便保存前校验未完成的内容。 */
export function TemplateInspector({ track, draft, catalog, duration, tab, onTabChange, onTimingChange, onEffectChange, onRemove, onClose }: {
  track: EffectTrack;
  draft: EffectDraft;
  catalog: EffectAsset[];
  duration: number;
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  onTimingChange: (timing: Pick<EffectTrack, "start_mode" | "start" | "duration">) => void;
  onEffectChange: (draft: EffectDraft) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const id = useId();
  const hasKeywords = track.target === "title" || track.target === "subtitle";
  const isTransition = track.target === "transition";
  const tabs: InspectorTab[] = isTransition ? ["timing"] : hasKeywords ? ["appearance", "timing", "keyword"] : ["appearance", "timing"];
  const activeTab = tabs.includes(tab) ? tab : tabs[0];
  const effectId = { title: track.editor.titleFlower, subtitle: track.editor.subtitleFlower, bubble: track.editor.bubble, filter: track.editor.filter, vfx: track.editor.vfx, transition: track.editor.transition }[track.target];
  const effect = catalog.find((asset) => asset.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : "未设置效果");
  return <section aria-label="画面对象设置" className="min-w-0">
    <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3">
      <div className="flex min-w-0 items-center gap-2.5"><ObjectIcon target={track.target} /><div className="min-w-0"><h2 className="text-[13px] font-semibold leading-5">{effectTargets[track.target]}</h2><p className="mt-0.5 truncate text-[11px] leading-3 text-muted-foreground">{effect}</p></div></div>
      <Hint label="关闭特效设置"><Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label="关闭特效设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    <div role="tablist" aria-label={`${effectTargets[track.target]}设置分类`} className={`grid border-b px-4 ${isTransition ? "grid-cols-1" : hasKeywords ? "grid-cols-3" : "grid-cols-2"}`}>{tabs.map((value) => <button key={value} type="button" role="tab" id={`${id}-${value}`} aria-controls={`${id}-panel-${value}`} aria-selected={activeTab === value} onClick={() => onTabChange(value)} className="border-b-2 border-transparent px-1 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring data-[selected=true]:border-primary data-[selected=true]:text-primary" data-selected={activeTab === value}>{value === "appearance" ? "外观与效果" : value === "timing" ? "时间设置" : "关键词设置"}</button>)}</div>
    {!isTransition && <div id={`${id}-panel-appearance`} role="tabpanel" aria-labelledby={`${id}-appearance`} hidden={activeTab !== "appearance"}><EffectEditor draft={draft} catalog={catalog} onChange={onEffectChange} target={track.target} onRemove={onRemove} onClose={onClose} view="appearance" showHeader={false} /></div>}
    <div id={`${id}-panel-timing`} role="tabpanel" aria-labelledby={`${id}-timing`} hidden={activeTab !== "timing"}><TrackTiming track={track} duration={duration} onChange={onTimingChange} /></div>
    {isTransition && <div className="template-inspector-fields p-4"><Button type="button" variant="outline" className="template-inspector-remove w-full" onClick={onRemove}>移除当前画面对象</Button></div>}
    {hasKeywords && <div id={`${id}-panel-keyword`} role="tabpanel" aria-labelledby={`${id}-keyword`} hidden={activeTab !== "keyword"}><EffectEditor draft={draft} catalog={catalog} onChange={onEffectChange} target={track.target} onRemove={onRemove} onClose={onClose} view="keyword" showHeader={false} /></div>}
  </section>;
}
