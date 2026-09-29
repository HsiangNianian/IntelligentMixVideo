/** 生成配置草稿与服务端 composition 的转换；空值和越界输入保留在表单，不提交任务。 */
import type { Composition } from "./model";

/** 画布预设只设置初始尺寸，自定义尺寸由用户输入。 */
export const canvasPresets = {
  portrait: { label: "竖屏 9:16", width: "1080", height: "1920" },
  landscape: { label: "横屏 16:9", width: "1920", height: "1080" },
  square: { label: "方形 1:1", width: "1080", height: "1080" },
};

/** 数字保留为字符串，允许清空或编辑中间值而不恢复成旧配置。 */
export interface CompositionDraft {
  canvas: keyof typeof canvasPresets | "custom";
  width: string;
  height: string;
  seconds: string;
}

/** 每个空白会话独立使用竖屏、30 FPS、5 秒默认配置。 */
export function defaultComposition(): CompositionDraft {
  return {
    canvas: "portrait",
    width: "1080",
    height: "1920",
    seconds: "5",
  };
}

/** 对齐服务端尺寸、像素与时长边界；秒数四舍五入到整帧并返回实际渲染配置。 */
export function resolveComposition(
  draft: CompositionDraft,
):
  | { composition: Composition; error: null }
  | { composition: null; error: string } {
  const width = 1080;
  const height = 1920;
  const fps = 30;
  const seconds = Number(draft.seconds);
  if (draft.width !== "1080" || draft.height !== "1920")
    return {composition: null, error: "主画布固定为 1080×1920。"};
  const frames = Math.round(seconds * fps);
  if (
    !draft.seconds.trim() ||
    !Number.isFinite(seconds) ||
    seconds <= 0 ||
    seconds > 30 ||
    frames < 1 ||
    frames > 1800
  )
    return {
      composition: null,
      error: "时长须大于 0 且不超过 30 秒，换算后至少为 1 帧。",
    };
  return {
    composition: { width, height, fps, duration_in_frames: frames },
    error: null,
  };
}

/** 概览使用真实整帧时长，不把用户输入的近似秒数当作最终时长。 */
export function compositionSummary(value: Composition): string {
  const seconds = Number((value.duration_in_frames / value.fps).toFixed(3));
  return `${value.width}×${value.height} · ${value.fps} FPS · ${seconds} 秒（${value.duration_in_frames} 帧）`;
}
