/** 上下纯色蒙版的配置、整数像素几何与 SDK 图片；基础预览复用同一几何。 */
/** 蒙版的编辑器对象 ID；冒号不在持久化轨道 ID 的字符范围内，避免选择和删除串到真实轨道。 */
export const FRAME_MASK_OBJECT_ID = "mask:frame";

export interface FrameMaskBar { enabled: boolean; height: number; color: string }
export interface FrameMask { top: FrameMaskBar; bottom: FrameMaskBar }

/** 首次选择按参考视频比例启用两栏，每个模板持有独立配置。 */
export function newFrameMask(): FrameMask {
  return {
    top: { enabled: true, height: 18.4, color: "#0E2C42" },
    bottom: { enabled: true, height: 16.6, color: "#0E2C42" },
  };
}

/** 在模板通信边界校验；内部编辑与渲染直接使用类型化配置。 */
export function validateFrameMask(mask: FrameMask): FrameMask {
  for (const bar of [mask.top, mask.bottom])
    if (!bar || typeof bar.enabled !== "boolean" || typeof bar.height !== "number" || !Number.isFinite(bar.height) || bar.height < 0 || bar.height > 50 || !/^#[0-9a-f]{6}$/i.test(bar.color))
      throw new Error("蒙版须设置独立开关、0～50% 的高度和六位十六进制颜色");
  return mask;
}

/** 百分比先四舍五入到成片像素，与 Python 合成保持相同边界。 */
export function frameMaskRects(mask: FrameMask | undefined, height: number) {
  return (["top", "bottom"] as const).flatMap((edge) => {
    const bar = mask?.[edge];
    const size = bar?.enabled ? Math.round(height * bar.height / 100) : 0;
    return size > 0 ? [{ edge, y: edge === "top" ? 0 : height - size, height: size, color: bar!.color }] : [];
  });
}

/** SDK 已支持 data:image 素材；只在载入时间线时生成一张透明背景 PNG。 */
export function frameMaskImage(mask: FrameMask | undefined, width: number, height: number): string | undefined {
  const rects = frameMaskRects(mask, height);
  if (!rects.length) return undefined;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("无法生成蒙版预览");
  for (const rect of rects) {
    context.fillStyle = rect.color;
    context.fillRect(0, rect.y, width, rect.height);
  }
  return canvas.toDataURL("image/png");
}
