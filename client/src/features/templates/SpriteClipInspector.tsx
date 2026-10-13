/** 模板里一次 Remotion 摆放的设置：资产对外的参数（可逐项恢复默认值）、宿主变换与起点；修改只进入草稿，随模板保存时服务端再按发布契约检查。 */
import { RotateCcw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { ParameterControl, type ParameterValue } from "@/components/ParameterControl";
import type { SpriteAsset } from "@/features/sprites/model";
import { CATEGORIES, categoryOf, transformField, transformFields, type TransformField } from "@/features/sprites/parameters";
import type { RemotionClip } from "./model";

/** 选中片段的右栏；资产已不在目录中时只允许移除。 */
export function SpriteClipInspector({ clip, asset, duration, onParameter, onTransform, onStart, onRemove, onClose }: {
  clip: RemotionClip;
  asset: SpriteAsset | undefined;
  duration: number | undefined;
  onParameter: (key: string, value: ParameterValue | undefined) => void;
  onTransform: (patch: Partial<Record<TransformField, number | undefined>>) => void;
  onStart: (start: number) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const category = asset ? CATEGORIES.find((item) => item.id === categoryOf(asset.kind))?.label : undefined;
  return <section aria-label="Remotion 资产设置" className="min-w-0">
    <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3">
      <div className="min-w-0"><h2 className="truncate text-[13px] font-semibold leading-5">{asset?.name ?? "未找到的资产"}</h2><p className="mt-0.5 text-[11px] leading-3 text-muted-foreground">{category ?? "资产已不可用，保存前请移除"}</p></div>
      <Hint label="关闭资产设置"><Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label="关闭资产设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    {asset && <>
      <fieldset className="space-y-3 border-t px-4 py-3">
        <legend className="sr-only">资产参数</legend>
        <h3 className="text-xs font-medium">参数</h3>
        {!asset.parameters.length && <p className="text-[11px] text-muted-foreground">这个资产没有可调整的参数。</p>}
        {asset.parameters.map(({ field, editable, defaultValue }) => {
          const changed = Object.prototype.hasOwnProperty.call(clip.parameters, field.key);
          return <div key={field.key} className="flex items-end gap-1.5">
            <ParameterControl className="min-w-0 flex-1" field={field} value={changed ? clip.parameters[field.key]! : defaultValue} disabled={!editable}
              onChange={(value) => onParameter(field.key, JSON.stringify(value) === JSON.stringify(defaultValue) ? undefined : value)} />
            {changed && <Hint label={`恢复默认：${field.label}`}><Button type="button" variant="ghost" size="icon" className="mb-0.5 size-8 shrink-0" aria-label={`恢复默认：${field.label}`} onClick={() => onParameter(field.key, undefined)}><RotateCcw className="size-3.5" aria-hidden="true" /></Button></Hint>}
          </div>;
        })}
      </fieldset>
      <fieldset className="space-y-3 border-t px-4 py-3">
        <legend className="sr-only">位置与变换</legend>
        <div className="flex items-center justify-between"><h3 className="text-xs font-medium">{transformFields(asset.kind).length === 1 ? "强度" : "位置与变换"}</h3>
          {Object.keys(clip.transform).length > 0 && <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={() => onTransform({ x: undefined, y: undefined, scale: undefined, rotation: undefined, opacity: undefined })}>恢复默认</Button>}
        </div>
        {transformFields(asset.kind).map((name) => {
          const { fallback, ...field } = transformField(name, asset.kind, asset.width, asset.height);
          return <ParameterControl key={name} field={field} value={clip.transform[name] ?? fallback(asset.box)} disabled={false}
            onChange={(value) => onTransform({ [name]: value as number })} />;
        })}
        {transformFields(asset.kind).length > 1 && <p className="text-[11px] text-muted-foreground">也可以在预览画面上拖动所选资产调整位置。</p>}
      </fieldset>
    </>}
    <div className="space-y-3 border-t px-4 py-3">
      <ParameterControl field={{ key: "start", label: "开始时间（秒）", kind: "number", minimum: 0, maximum: duration }} value={clip.start} disabled={false}
        onChange={(value) => onStart(value as number)} />
      <Button type="button" variant="outline" className="w-full gap-1.5" onClick={onRemove}><Trash2 className="size-3.5" aria-hidden="true" />移除这个资产</Button>
    </div>
  </section>;
}
