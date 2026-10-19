/**
 * IMS 关键词字效设置：标题、字幕的「关键词设置」页签与它们的关键词对象共用。开启「关键词单独字效」后关键词作为独立的
 * IMS 文字显示，在这里选花字，动画、字号与位置在关键词对象里设置。关键词对象不在时间轴与对象列表里单独成行。
 * 组件只展示与回调，草稿修改由工作区完成。
 */
import { useId } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { EffectCombobox } from "./EffectCombobox";
import type { EffectAsset, EffectTrack } from "./model";

/**
 * `keyword` 是这个标题或字幕的关键词对象（从未开启时为空），`flower` 是它当前的花字（未选为空串），`flowers` 是可选的
 * 花字目录；`editing` 表示当前已经选中关键词对象，此时不再显示「编辑关键词」入口。
 */
export function KeywordEffectPanel({ role, keyword, flower, flowers, editing, onEnabled, onFlower, onEdit }: {
  role: "title" | "subtitle";
  keyword?: EffectTrack;
  flower: string;
  flowers: EffectAsset[];
  editing: boolean;
  onEnabled: (enabled: boolean) => void;
  onFlower: (value: string) => void;
  onEdit: () => void;
}) {
  const id = useId();
  const noun = role === "title" ? "标题" : "字幕";
  const enabled = !!keyword && keyword.enabled !== false;
  return <section aria-label="关键词字效" className="space-y-3 border-t px-4 py-3">
    <div className="flex items-center justify-between gap-2">
      <Label htmlFor={`${id}-enabled`} className="text-xs font-medium">关键词单独字效</Label>
      <Switch id={`${id}-enabled`} checked={enabled} onCheckedChange={onEnabled} />
    </div>
    {!enabled ? <p className="text-[11px] text-muted-foreground">开启后关键词作为独立文字显示，可单独设置花字、动画、字号与位置；句中的关键词仍按上面的样式显示。</p> : <>
      <div className="space-y-1.5">
        <Label htmlFor={`${id}-flower`} className="text-xs">关键词花字</Label>
        <EffectCombobox id={`${id}-flower`} value={flower} placeholder="无花字" searchPlaceholder="搜索关键词花字" onChange={onFlower}
          options={[{ value: "", label: "无花字" }, ...flowers.map((asset) => ({ value: asset.id, label: asset.name, hint: asset.effect_id }))]} />
        <p className="text-[11px] text-muted-foreground">动画、字号与位置在「{noun}关键词」里设置。</p>
      </div>
      {!editing && <Button type="button" variant="outline" size="sm" onClick={onEdit}>编辑{noun}关键词</Button>}
    </>}
  </section>;
}
