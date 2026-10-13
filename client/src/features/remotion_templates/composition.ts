/** 本链路固定的主画布规格；画布与帧率由服务端契约决定，前端只提供生成前的时长选择。 */
import type { Composition } from "./model";

/** 主画布固定为竖屏 1080×1920、30 FPS；五秒是默认时长。 */
export const FIXED_COMPOSITION: Composition = {
  width: 1080,
  height: 1920,
  fps: 30,
  duration_in_frames: 150,
};

/** 常用时长（秒）；其余时长通过自定义输入。 */
export const DURATION_PRESETS = [3, 5, 8, 10, 15];
/** 默认时长（秒），由 FIXED_COMPOSITION 的初始帧数换算，聊天面板的初始帧数与选择器默认项同源。 */
export const DEFAULT_DURATION_SECONDS = FIXED_COMPOSITION.duration_in_frames / FIXED_COMPOSITION.fps;
/** 服务端允许的最长预览时长（秒）。 */
export const MAX_DURATION_SECONDS = 30;

/** 四舍五入到整帧后按服务端契约校验：至少 1 帧、不超过 30 秒的帧数；无法解析或范围外返回 null。 */
export function durationFrames(seconds: number): number | null {
  const frames = Math.round(seconds * FIXED_COMPOSITION.fps);
  return Number.isFinite(frames) && frames >= 1 && frames <= MAX_DURATION_SECONDS * FIXED_COMPOSITION.fps ? frames : null;
}

/** 首次创建显式携带固定规格与所选时长，避免客户端与服务端默认值漂移。 */
export function defaultComposition(frames = FIXED_COMPOSITION.duration_in_frames): Composition {
  return { ...FIXED_COMPOSITION, duration_in_frames: frames };
}

/** 说明固定主画布，供生成前提示使用。 */
export function fixedCompositionSummary(): string {
  return `${FIXED_COMPOSITION.width}×${FIXED_COMPOSITION.height} · ${FIXED_COMPOSITION.fps} FPS`;
}

/** 展示成功版本真实的整帧时长，包括历史版本原有的画布规格。 */
export function compositionSummary(value: Composition): string {
  const seconds = Number((value.duration_in_frames / value.fps).toFixed(3));
  return `${value.width}×${value.height} · ${value.fps} FPS · ${seconds} 秒（${value.duration_in_frames} 帧）`;
}
