/** 本链路固定的主画布规格；画布、帧率与初始时长由服务端契约决定，前端不提供选择。 */
import type { Composition } from "./model";

/** 主画布固定为竖屏 1080×1920、30 FPS；五秒只作为开始规划前的初始时长。 */
export const FIXED_COMPOSITION: Composition = {
  width: 1080,
  height: 1920,
  fps: 30,
  duration_in_frames: 150,
};

/** 首次创建显式携带固定规格，避免客户端与服务端默认值漂移。 */
export function defaultComposition(): Composition {
  return { ...FIXED_COMPOSITION };
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
