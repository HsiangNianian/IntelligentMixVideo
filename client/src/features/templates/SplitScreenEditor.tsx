/** 同屏布局编辑：位置用画布百分比，圆角和内边框用 1080×1920 基准像素。 */
import { Input } from "@/components/ui/input";
import { defaultSplitScreen, type SplitScreen, type ScreenWindow } from "./splitScreen";

/** 参数直接保存在草稿；原生表单和保存入口共同阻止无效值提交。 */
export function SplitScreenEditor({ value, onChange }: { value?: SplitScreen; onChange: (value: SplitScreen) => void }) {
  const layout = value ?? defaultSplitScreen();
  const number = (label: string, value: number, onChange: (n: number) => void, max = 100, min = 0) => <label className="space-y-1 text-xs">{label}<Input aria-label={label} type="number" min={min} max={max} step="any" required value={Number.isNaN(value) ? "" : value} onChange={(event) => onChange(event.target.valueAsNumber)} /></label>;
  const window = (role: "material" | "avatar", label: string) => {
    const w = layout[role];
    const update = (change: Partial<ScreenWindow>) => onChange({ ...layout, [role]: { ...w, ...change } });
    const limit = Math.min(w.width * 10.8, w.height * 19.2) / 2;
    return <fieldset className="space-y-2"><legend className="mb-2 text-xs font-semibold">{label}</legend><div className="grid grid-cols-2 gap-2">
      {number(`${label}左侧 %`, w.x, (x) => update({ x }), 100 - w.width)}
      {number(`${label}顶部 %`, w.y, (y) => update({ y }), 100 - w.height)}
      {number(`${label}宽度 %`, w.width, (width) => update({ width }), 100 - w.x, 0.1)}
      {number(`${label}高度 %`, w.height, (height) => update({ height }), 100 - w.y, 0.1)}
      {number(`${label}圆角 px`, w.radius, (radius) => update({ radius }), limit)}
      {number(`${label}边框 px`, w.borderWidth, (borderWidth) => update({ borderWidth }), limit)}
      <label className="space-y-1 text-xs">显示方式<select aria-label={`${label}显示方式`} className="h-8 w-full rounded-md border bg-background px-2" value={w.fit} onChange={(event) => update({ fit: event.target.value as ScreenWindow["fit"] })}><option value="Contain">完整显示</option><option value="Cover">裁剪填满</option></select></label>
      <label className="space-y-1 text-xs">边框颜色<Input aria-label={`${label}边框颜色`} type="color" value={w.borderColor} onChange={(event) => update({ borderColor: event.target.value })} /></label>
    </div></fieldset>;
  };
  return <details className="border-b p-4"><summary className="cursor-pointer text-sm font-semibold">素材与数字人同屏{layout.enabled ? " · 已开启" : ""}</summary><div className="mt-3 space-y-4">
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={layout.enabled} onChange={(event) => onChange({ ...layout, enabled: event.target.checked })} />启用同屏</label>
    <fieldset disabled={!layout.enabled} className="space-y-4 disabled:opacity-50">
      <label className="flex items-center justify-between gap-3 text-xs">背景颜色<Input className="w-16" type="color" aria-label="同屏背景颜色" value={layout.backgroundColor} onChange={(event) => onChange({ ...layout, backgroundColor: event.target.value })} /></label>
      {window("material", "素材")}{window("avatar", "数字人")}
      {(["subtitle", "subtitleEn"] as const).map((role) => <fieldset key={role}><legend className="mb-2 text-xs font-semibold">同屏{role === "subtitle" ? "中文" : "英文"}字幕</legend><div className="grid grid-cols-2 gap-2">
        {number(`${role === "subtitle" ? "中文" : "英文"}字幕中心 X %`, layout[role].x, (x) => onChange({ ...layout, [role]: { ...layout[role], x } }))}
        {number(`${role === "subtitle" ? "中文" : "英文"}字幕顶部 Y %`, layout[role].y, (y) => onChange({ ...layout, [role]: { ...layout[role], y } }))}
      </div></fieldset>)}
    </fieldset>
    <p className="text-xs leading-relaxed text-muted-foreground">仅命中素材时同屏；纯素材合成忽略此设置。同屏不使用转场，重叠时数字人在上。完整显示的圆角和边框跟随画面，留白显示背景色。</p>
  </div></details>;
}
