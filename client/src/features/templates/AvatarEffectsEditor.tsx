/** 开场与抠像开关；预览只展示布局，真实定位及人物边缘以成片为准。 */
import { Input } from "@/components/ui/input";
import { defaultAvatarEffects, type AvatarEffects } from "./avatarEffects";

/** 将开关和参数写回草稿；同屏与圆形模式决定哪些控件可编辑。 */
export function AvatarEffectsEditor({ value, sameScreen, circle = false, onChange }: { value?: AvatarEffects; sameScreen: boolean; circle?: boolean; onChange: (value: AvatarEffects) => void }) {
  const config = value ?? defaultAvatarEffects();
  const update = (patch: Partial<AvatarEffects>) => onChange({ ...config, ...patch });
  return <details className="border-b p-4"><summary className="cursor-pointer text-sm font-semibold">数字人开场与抠像</summary><div className="mt-3 space-y-3">
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={config.openingEnabled} onChange={(event) => update({ openingEnabled: event.target.checked })} />启用圆形开场</label>
    <fieldset className="grid grid-cols-2 gap-2">
      {([['openingDuration', '开场时长（秒）', 0.1, 300, 0.1], ['circleScale', '头部圆形留量（倍）', 1, 3, 0.1], ['feather', '圆边羽化', 0, 0.2, 0.01]] as const).map(([key, label, min, max, step]) =>
        <label key={key} className="space-y-1 text-xs">{label}<Input aria-label={label} type="number" min={min} max={max} step={step} required disabled={!config.openingEnabled && (key === "openingDuration" || !circle)} value={Number.isNaN(config[key]) ? "" : config[key]} onChange={(event) => update({ [key]: event.target.valueAsNumber })} /></label>)}
    </fieldset>
    <p className="text-xs leading-relaxed text-muted-foreground">按每条数字人视频自动定位固定圆。有效开场内隐藏素材，圆外黑底；检测不到人脸则跳过，开场结束后保留素材原播放进度。</p>
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={config.removeBackground} disabled={!sameScreen || circle} onChange={(event) => update({ removeBackground: event.target.checked })} />同屏时去除数字人背景</label>
    <p className="text-xs leading-relaxed text-muted-foreground">{circle ? "圆形头像保留圆内背景，不使用抠像；与开场共用片头检测的固定圆、圆形留量和羽化。" : sameScreen ? "人物位置和大小沿用同屏数字人窗口；抠像时圆角和矩形边框不生效。" : "先启用“素材与数字人同屏”，再设置抠像。"}预览仅用于调整布局，自动圆形开场和抠像效果以成片为准；圆形头像预览以居中圆形示意。</p>
  </div></details>;
}
