/** 蒙版素材卡片与右侧参数；上下栏分别选择纯色或双色渐变，文字与视频构图继续由原功能负责。 */
import { X } from "lucide-react";
import { Hint } from "@/components/Hint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { FrameMask, FrameMaskGradient } from "./frameMask";
import { RemoveObjectButton } from "./RemoveObjectButton";

/** 蒙版页签只列出实际可用的类型，重复选择保留当前参数。 */
export function FrameMaskCard({ mask, onSelect }: { mask?: FrameMask; onSelect: () => void }) {
  return <div className="space-y-3 p-4">
    <p className="text-xs text-muted-foreground">在原视频上叠加纯色或渐变背景，不改变视频构图。</p>
    <button type="button" aria-label="选择上下蒙版" onClick={onSelect}
      className="w-full rounded-lg border bg-background p-3 text-left transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
      <div className="relative mx-auto mb-3 h-28 w-20 overflow-hidden rounded bg-muted" aria-hidden="true">
        {(["top", "bottom"] as const).map((edge) => {
          const bar = mask?.[edge], gradient = bar?.gradient;
          return <div key={edge} className={`absolute inset-x-0 ${edge === "top" ? "top-0 h-[18.4%]" : "bottom-0 h-[16.6%]"}`}
            style={{ background: gradient?.enabled ? `linear-gradient(to ${gradient.direction === "up" ? "top" : "bottom"}, ${bar!.color} 0%, ${gradient.endColor} ${gradient.range}%)` : bar?.color ?? "#0E2C42" }} />;
        })}
      </div>
      <span className="text-sm font-medium">上下蒙版</span>
      <span className="mt-1 block text-xs text-muted-foreground">{mask ? "点击设置高度、纯色 / 渐变与开关" : "点击添加，整段显示"}</span>
    </button>
  </div>;
}

/** 每条栏最多占半屏；实时更新草稿，关闭保留其配置。 */
export function FrameMaskPanel({ mask, onChange, onRemove, onClose }: { mask: FrameMask; onChange: (mask: FrameMask) => void; onRemove: () => void; onClose: () => void }) {
  return <section aria-label="上下蒙版设置" className="space-y-5 p-4">
    <div className="flex items-center justify-between">
      <h2 className="text-[13px] font-semibold">上下蒙版</h2>
      <Hint label="关闭蒙版设置"><Button type="button" variant="ghost" size="icon-xs" aria-label="关闭蒙版设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    {(["top", "bottom"] as const).map((edge) => {
      const bar = mask[edge];
      const label = edge === "top" ? "上栏" : "下栏";
      const gradient: FrameMaskGradient = bar.gradient ?? { enabled: false, endColor: "#07101F", direction: edge === "top" ? "up" : "down", range: 100 };
      const colorLabel = `${label}${gradient.enabled ? "起始色" : "颜色"}`;
      return <div key={edge} className="space-y-3">
        <div className="flex items-center justify-between"><Label>{label}</Label><Switch aria-label={`启用${label}`} checked={bar.enabled} onCheckedChange={(enabled) => onChange({ ...mask, [edge]: { ...bar, enabled } })} /></div>
        <ToggleGroup type="single" variant="outline" size="sm" aria-label={`${label}填充方式`} value={gradient.enabled ? "gradient" : "solid"}
          onValueChange={(value) => { if (value) onChange({ ...mask, [edge]: { ...bar, gradient: { ...gradient, enabled: value === "gradient" } } }); }}>
          <ToggleGroupItem value="solid" aria-label={`${label}纯色`}>纯色</ToggleGroupItem>
          <ToggleGroupItem value="gradient" aria-label={`${label}渐变`}>渐变</ToggleGroupItem>
        </ToggleGroup>
        <div className="space-y-2">
          <Label>{label}高度 / 画布百分比</Label>
          <div className="flex items-center gap-3">
            <Slider label={`${label}高度滑块`} min={0} max={50} step={0.1} value={[bar.height]} className="flex-1"
              onValueChange={([height]) => onChange({ ...mask, [edge]: { ...bar, height } })} />
            <Input aria-label={`${label}高度`} type="number" min={0} max={50} step={0.1} value={bar.height} className="w-20"
              onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
              onChange={(event) => {
                const value = event.target.value;
                if (value !== "" && Number.isFinite(Number(value))) onChange({ ...mask, [edge]: { ...bar, height: Math.max(0, Math.min(50, Number(value))) } });
              }} />
          </div>
        </div>
        <div className="flex items-center gap-3"><Label>{colorLabel}</Label><Input aria-label={colorLabel} type="color" value={bar.color}
          onChange={(event) => onChange({ ...mask, [edge]: { ...bar, color: event.target.value } })} className="h-8 w-12 p-1" /><span className="text-xs text-muted-foreground">{bar.color.toUpperCase()}</span></div>
        {gradient.enabled && <>
          <div className="flex items-center gap-3"><Label>{label}结束色</Label><Input aria-label={`${label}结束色`} type="color" value={gradient.endColor}
            onChange={(event) => onChange({ ...mask, [edge]: { ...bar, gradient: { ...gradient, endColor: event.target.value } } })} className="h-8 w-12 p-1" /><span className="text-xs text-muted-foreground">{gradient.endColor.toUpperCase()}</span></div>
          <div className="space-y-2"><Label>{label}渐变方向</Label>
            <ToggleGroup type="single" variant="outline" size="sm" aria-label={`${label}渐变方向`} value={gradient.direction}
              onValueChange={(direction) => { if (direction === "up" || direction === "down") onChange({ ...mask, [edge]: { ...bar, gradient: { ...gradient, direction } } }); }}>
              <ToggleGroupItem value="down" aria-label={`${label}上到下`}>上 → 下</ToggleGroupItem>
              <ToggleGroupItem value="up" aria-label={`${label}下到上`}>下 → 上</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <div className="space-y-2"><Label>{label}渐变范围 / 栏高百分比</Label>
            <div className="flex items-center gap-3">
              <Slider label={`${label}渐变范围滑块`} min={1} max={100} step={1} value={[gradient.range]} className="flex-1"
                onValueChange={([range]) => onChange({ ...mask, [edge]: { ...bar, gradient: { ...gradient, range } } })} />
              <Input aria-label={`${label}渐变范围`} type="number" min={1} max={100} step={1} value={gradient.range} className="w-20"
                onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value !== "" && Number.isFinite(Number(value))) onChange({ ...mask, [edge]: { ...bar, gradient: { ...gradient, range: Math.max(1, Math.min(100, Math.round(Number(value)))) } } });
                }} />
            </div>
            <p className="text-xs text-muted-foreground">100% 整栏过渡；50% 在前半栏过渡，后半栏保持结束色。</p>
          </div>
        </>}
      </div>;
    })}
    <p className="text-xs leading-relaxed text-muted-foreground">蒙版覆盖原视频并整段显示，现有文字和特效显示在其上方。关闭两栏可停用并保留参数，不再需要时可移除。</p>
    <RemoveObjectButton onRemove={onRemove} onClose={onClose} />
  </section>;
}
