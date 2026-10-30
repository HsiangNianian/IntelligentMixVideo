/** 数字人预览设置、圆形几何与蒙版资源；取景随模板保存，真实合成排除这些设置。 */
import { apiBase } from "@/lib/api-base";
import type { AvatarEffects } from "./avatarEffects";
import type { ScreenPoint } from "./splitScreen";

/** 编辑器来源槽位与源画面百分比圆心，与成片窗口摆放独立。 */
export interface AvatarPreview extends ScreenPoint { source: number }

/** 缺少预览设置的旧模板使用稳定默认值。 */
export const defaultAvatarPreview: AvatarPreview = { x: 50, y: 50, source: 0 };

/** 保存和读取边界检查预览取景；合成端不会使用该字段。 */
export function validateAvatarPreview(value: AvatarPreview): AvatarPreview {
  if (![value.x, value.y].every((n) => Number.isFinite(n) && n >= 0 && n <= 100) || !Number.isInteger(value.source) || value.source < 0 || value.source > 2) throw new Error("预览取景 X/Y 须为 0～100，来源为视频 1～3");
  return value;
}

/** 固定示意头部直径为源短边的 25%；圆心使用源宽高百分比，限制圆形完整位于源内。 */
export function previewHeadCircle(source: { width: number; height: number }, scale: number, point: ScreenPoint = { x: 50, y: 50 }) {
  const radius = Math.min(source.width, source.height) * 0.25 * scale / 2;
  return { x: Math.max(radius, Math.min(source.width - radius, point.x / 100 * source.width)),
    y: Math.max(radius, Math.min(source.height - radius, point.y / 100 * source.height)), radius };
}

/** 开场按预览 30fps 对齐且不延长片长；只改变显示，不移动原素材进度。 */
export function previewOpeningDuration(config: AvatarEffects | undefined, duration: number): number {
  return config?.openingEnabled ? Math.min(duration, Math.max(1, Math.round(config.openingDuration * 30)) / 30) : 0;
}

/** SDK 蒙版只传有界几何；源视频地址和视频帧均不进入请求。 */
export function previewCircleMaskUrl(source: { width: number; height: number; duration: number }, circle: ReturnType<typeof previewHeadCircle>, feather: number): string {
  const query = new URLSearchParams(Object.entries({ width: source.width, height: source.height,
    x: circle.x / source.width, y: circle.y / source.height, radius: circle.radius / Math.min(source.width, source.height),
    feather, seconds: Math.ceil(source.duration) + 1,
  }).map(([key, value]) => [key, String(value)]));
  return `${apiBase()}/template/preview-circle-mask?${query}`;
}

/** 基础播放器在原视频坐标里绘制相同的向内线性羽化，缩放或裁剪后仍保持正圆。 */
export function previewCircleGradient(source: { width: number; height: number }, circle: ReturnType<typeof previewHeadCircle>, feather: number): string {
  return `radial-gradient(ellipse ${circle.radius / source.width * 100}% ${circle.radius / source.height * 100}% at ${circle.x / source.width * 100}% ${circle.y / source.height * 100}%, #fff ${(1 - 2 * feather) * 100}%, transparent 100%)`;
}
