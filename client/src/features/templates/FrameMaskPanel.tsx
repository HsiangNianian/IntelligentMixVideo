/** 蒙版素材卡片与右侧参数；图片成功上传才替换填充，关闭面板取消未完成上传，颜色和图片共用栏高。 */
import { useEffect, useRef, useState } from "react";
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
import { frameMaskImageUrl, uploadFrameMaskImage } from "./api";

/** 服务端已统一裁成 9:16，缩略图随容器拉伸，与正式填充保持相同取景。 */
function ImageThumbnail({ imageId, label }: { imageId: string; label: string }) {
  return <img src={frameMaskImageUrl(imageId)} alt={label} className="size-full object-fill" />;
}

/** 蒙版页签只列出实际可用的类型，重复选择保留当前参数。 */
export function FrameMaskCard({ mask, onSelect }: { mask?: FrameMask; onSelect: () => void }) {
  return <div className="space-y-3 p-4">
    <p className="text-xs text-muted-foreground">在原视频上叠加纯色、渐变或图片背景，不改变视频构图。</p>
    <button type="button" aria-label="选择上下蒙版" onClick={onSelect}
      className="w-full rounded-lg border bg-background p-3 text-left transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
      <div className="relative mx-auto mb-3 h-28 w-20 overflow-hidden rounded bg-muted" aria-hidden="true">
        {(["top", "bottom"] as const).map((edge) => {
          const bar = mask?.[edge], gradient = bar?.gradient;
          return <div key={edge} className={`absolute inset-x-0 ${edge === "top" ? "top-0 h-[18.4%]" : "bottom-0 h-[16.6%]"}`}
            style={{ background: bar?.imageId ? undefined : gradient?.enabled ? `linear-gradient(to ${gradient.direction === "up" ? "top" : "bottom"}, ${bar!.color} 0%, ${gradient.endColor} ${gradient.range}%)` : bar?.color ?? "#0E2C42" }}>
            {bar?.imageId && <ImageThumbnail imageId={bar.imageId} label={`${edge === "top" ? "上栏" : "下栏"}图片`} />}
          </div>;
        })}
      </div>
      <span className="text-sm font-medium">上下蒙版</span>
      <span className="mt-1 block text-xs text-muted-foreground">{mask ? "点击设置高度、填充方式与开关" : "点击添加，整段显示"}</span>
    </button>
  </div>;
}

/** 每条栏最多占半屏；实时更新草稿，关闭保留其配置。 */
export function FrameMaskPanel({ mask, onChange, onRemove, onClose, onUploadingChange }: { mask: FrameMask; onChange: (mask: FrameMask) => void; onRemove: () => void; onClose: () => void; onUploadingChange?: (uploading: boolean) => void }) {
  const [uploading, setUploading] = useState<"top" | "bottom" | null>(null);
  const [error, setError] = useState("");
  const inputs = useRef<Partial<Record<"top" | "bottom", HTMLInputElement>>>({});
  const upload = useRef<AbortController | null>(null);
  const latest = useRef({ mask, onChange, onUploadingChange });
  latest.current = { mask, onChange, onUploadingChange };
  useEffect(() => () => { upload.current?.abort(); latest.current.onUploadingChange?.(false); }, []);

  /** 同时只上传一张；失败不改草稿，成功合并最新栏高，关闭后的迟到结果不再生效。 */
  async function selectImage(edge: "top" | "bottom", file: File) {
    const controller = new AbortController();
    upload.current?.abort();
    upload.current = controller;
    setUploading(edge); setError(""); latest.current.onUploadingChange?.(true);
    try {
      const imageId = await uploadFrameMaskImage(file, controller.signal);
      if (controller.signal.aborted) return;
      const current = latest.current.mask;
      latest.current.onChange({ ...current, [edge]: { ...current[edge], imageId } });
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "图片上传失败，请重试");
    } finally {
      if (!controller.signal.aborted) { setUploading(null); latest.current.onUploadingChange?.(false); }
    }
  }
  return <section aria-label="上下蒙版设置" className="space-y-5 p-4">
    <div className="flex items-center justify-between">
      <h2 className="text-[13px] font-semibold">上下蒙版</h2>
      <Hint label="关闭蒙版设置"><Button type="button" variant="ghost" size="icon-xs" aria-label="关闭蒙版设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {(["top", "bottom"] as const).map((edge) => {
      const bar = mask[edge];
      const label = edge === "top" ? "上栏" : "下栏";
      const gradient: FrameMaskGradient = bar.gradient ?? { enabled: false, endColor: "#07101F", direction: edge === "top" ? "up" : "down", range: 100 };
      const colorLabel = `${label}${gradient.enabled ? "起始色" : "颜色"}`;
      return <div key={edge} className="space-y-3">
        <div className="flex items-center justify-between"><Label>{label}</Label><Switch aria-label={`启用${label}`} checked={bar.enabled} onCheckedChange={(enabled) => onChange({ ...mask, [edge]: { ...bar, enabled } })} /></div>
        <input type="file" accept=".png,.jpg,.jpeg,.webp" className="hidden" aria-label={`上传${label}图片`}
          ref={(input) => { if (input) inputs.current[edge] = input; }} disabled={uploading !== null}
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void selectImage(edge, file); }} />
        <ToggleGroup type="single" variant="outline" size="sm" disabled={uploading !== null} aria-label={`${label}填充方式`} value={bar.imageId ? "image" : gradient.enabled ? "gradient" : "solid"}
          onValueChange={(value) => {
            if (value === "image") { inputs.current[edge]?.click(); return; }
            if (value) onChange({ ...mask, [edge]: { ...bar, imageId: "", gradient: { ...gradient, enabled: value === "gradient" } } });
          }}>
          <ToggleGroupItem value="solid" aria-label={`${label}纯色`}>纯色</ToggleGroupItem>
          <ToggleGroupItem value="gradient" aria-label={`${label}渐变`}>渐变</ToggleGroupItem>
          <ToggleGroupItem value="image" aria-label={`${label}图片`}>图片</ToggleGroupItem>
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
        {uploading === edge && <p role="status" className="text-xs text-muted-foreground">正在上传{label}图片，成功后替换…</p>}
        {bar.imageId && <div className="space-y-2">
          <div className="h-20 overflow-hidden rounded border"><ImageThumbnail imageId={bar.imageId} label={`${label}图片缩略图`} /></div>
          <Button type="button" variant="outline" size="sm" disabled={uploading !== null} onClick={() => inputs.current[edge]?.click()}>更换{label}图片</Button>
          <p className="text-xs text-muted-foreground">图片先居中适配 9:16，再拉伸至栏高；透明区域透出视频。</p>
        </div>}
        {!bar.imageId && <div className="flex items-center gap-3"><Label>{colorLabel}</Label><Input aria-label={colorLabel} type="color" value={bar.color}
          onChange={(event) => onChange({ ...mask, [edge]: { ...bar, color: event.target.value } })} className="h-8 w-12 p-1" /><span className="text-xs text-muted-foreground">{bar.color.toUpperCase()}</span></div>}
        {!bar.imageId && gradient.enabled && <>
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
