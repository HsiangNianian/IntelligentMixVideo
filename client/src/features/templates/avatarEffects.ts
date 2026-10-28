/** 数字人开场与抠像配置；圆心由每次合成检测，窗口位置复用同屏配置。 */
export interface AvatarEffects {
  openingEnabled: boolean;
  openingDuration: number;
  circleScale: number;
  feather: number;
  removeBackground: boolean;
}

/** 为新草稿创建独立默认配置，开场和抠像均保持关闭。 */
export function defaultAvatarEffects(): AvatarEffects {
  return { openingEnabled: false, openingDuration: 2, circleScale: 1.6, feather: 0.02, removeBackground: false };
}

/** 保存边界校验，显式 false 和零羽化不能被默认值覆盖。 */
export function validateAvatarEffects(value: AvatarEffects): void {
  if (typeof value.openingEnabled !== "boolean" || typeof value.removeBackground !== "boolean") throw new Error("数字人效果开关无效");
  for (const [key, min, max] of [["openingDuration", 0.1, 300], ["circleScale", 1, 3], ["feather", 0, 0.2]] as const)
    if (!Number.isFinite(value[key]) || value[key] < min || value[key] > max) throw new Error("开场时长须为 0.1～300 秒，圆形留量为 1～3 倍，羽化为 0～0.2");
}
