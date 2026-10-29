/** 圆形蒙版入口与右侧设置；取景参数仅作用于预览，留量与羽化随模板保存。 */
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { defaultAvatarEffects, type AvatarEffects } from "./avatarEffects";
import type { ScreenPoint } from "./splitScreen";
import { RemoveObjectButton } from "./RemoveObjectButton";

/** 工作区保存的预览参数；回车不提交表单，来源与取景不影响成片定位。 */
export function AvatarPreviewControls({ source, center, onSourceChange, onCenterChange }: {
  source: number; center?: ScreenPoint; onSourceChange: (source: number) => void; onCenterChange: (center: ScreenPoint) => void;
}) {
  return <div className="space-y-3 text-xs">
    <label className="block space-y-1">数字人预览来源<select aria-label="数字人预览来源" value={source} onChange={(event) => onSourceChange(Number(event.target.value))} className="h-8 w-full rounded-md border bg-background px-2">{[0, 1, 2].map((index) => <option key={index} value={index}>视频 {index + 1}</option>)}</select></label>
    {center && <>
      <div className="grid grid-cols-2 gap-2">{(["x", "y"] as const).map((axis) => <label key={axis} className="space-y-1">预览取景 {axis.toUpperCase()}（%）<Input aria-label={`预览取景 ${axis.toUpperCase()}（%）`} type="number" min={0} max={100} step={1} value={center[axis]} onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} onChange={(event) => { const value = event.target.valueAsNumber; if (Number.isFinite(value)) onCenterChange({ ...center, [axis]: Math.max(0, Math.min(100, value)) }); }} /></label>)}</div>
      <p className="leading-relaxed text-muted-foreground">当前位置仅用于预览，成片会自动定位人脸。</p>
    </>}
  </div>;
}

/** 开场与同屏圆形头像共用留量、羽化；两处实际编辑同一份保存规则。 */
export function AvatarCircleControls({ value, onChange }: { value?: AvatarEffects; onChange: (value: AvatarEffects) => void }) {
  const config = value ?? defaultAvatarEffects();
  return <fieldset className="grid grid-cols-2 gap-2">
    {([['circleScale', '头部圆形留量（倍）', 1, 3, 0.1], ['feather', '圆边羽化', 0, 0.2, 0.01]] as const).map(([key, label, min, max, step]) =>
      <label key={key} className="space-y-1 text-xs">{label}<Input aria-label={label} type="number" min={min} max={max} step={step} required value={Number.isNaN(config[key]) ? "" : config[key]} onChange={(event) => onChange({ ...config, [key]: event.target.valueAsNumber })} /></label>)}
  </fieldset>;
}

/** 选中开场蒙版时展示；关闭面板保留效果，移除对象才停用开场。 */
export function AvatarEffectsEditor({ value, onChange, onClose, onRemove, children }: { value: AvatarEffects; onChange: (value: AvatarEffects) => void; onClose: () => void; onRemove: () => void; children?: ReactNode }) {
  return <section aria-label="圆形蒙版设置" className="space-y-3 border-b p-4">
    <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">圆形蒙版</h2><Button type="button" variant="ghost" size="icon-xs" aria-label="关闭圆形蒙版设置" onClick={onClose}><X className="size-3.5" /></Button></div>
    <label className="block space-y-1 text-xs">开场时长（秒）<Input aria-label="开场时长（秒）" type="number" min={0.1} max={300} step={0.1} required value={Number.isNaN(value.openingDuration) ? "" : value.openingDuration} onChange={(event) => onChange({ ...value, openingDuration: event.target.valueAsNumber })} /></label>
    <AvatarCircleControls value={value} onChange={onChange} />
    {children}
    <RemoveObjectButton onRemove={onRemove} onClose={onClose} />
  </section>;
}

/** 点击即添加圆形开场；重复选择只打开该对象，不重置参数。 */
export function AvatarMaskCard({ onSelect }: { onSelect: () => void }) {
  return <div className="px-4 pb-4"><button type="button" aria-label="添加圆形蒙版" onClick={onSelect}
    className="w-full rounded-lg border bg-background p-3 text-left transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
    <div className="mx-auto mb-3 flex h-28 w-20 items-center justify-center rounded bg-muted" aria-hidden="true"><span className="size-12 rounded-full border-2 border-foreground/50 bg-background" /></div>
    <span className="text-sm font-medium">圆形蒙版</span><span className="mt-1 block text-xs text-muted-foreground">点击添加，开场显示</span>
  </button></div>;
}
