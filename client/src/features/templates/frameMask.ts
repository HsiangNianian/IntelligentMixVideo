/** 上下蒙版的配置、整数像素几何与双色渐变 PNG；SDK 与基础预览复用同一绘制。 */
/** 蒙版的编辑器对象 ID；冒号不在持久化轨道 ID 的字符范围内，避免选择和删除串到真实轨道。 */
export const FRAME_MASK_OBJECT_ID = "mask:frame";

/** 渐变不改变透明度；range 为完成起始色到结束色过渡所占的栏高百分比。 */
export interface FrameMaskGradient { enabled: boolean; endColor: string; direction: "up" | "down"; range: number }
/** 单栏贴住画布边缘，color 同时作为渐变起始色；缺少 gradient 时保持旧模板纯色。 */
export interface FrameMaskBar { enabled: boolean; height: number; color: string; gradient?: FrameMaskGradient }
/** 上下栏独立设置，停用时保留样式。 */
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
  for (const bar of [mask.top, mask.bottom]) {
    if (!bar || typeof bar.enabled !== "boolean" || typeof bar.height !== "number" || !Number.isFinite(bar.height) || bar.height < 0 || bar.height > 50 || !/^#[0-9a-f]{6}$/i.test(bar.color))
      throw new Error("蒙版须设置独立开关、0～50% 的高度和六位十六进制颜色");
    const gradient = bar.gradient;
    if (gradient !== undefined && (!gradient || typeof gradient.enabled !== "boolean" || !/^#[0-9a-f]{6}$/i.test(gradient.endColor) || !["up", "down"].includes(gradient.direction) || !Number.isInteger(gradient.range) || gradient.range < 1 || gradient.range > 100))
      throw new Error("蒙版渐变须设置开关、六位十六进制结束色、上下方向和 1～100% 的整数范围");
  }
  return mask;
}

/** 百分比先四舍五入到成片像素，与 Python 合成保持相同边界。 */
export function frameMaskRects(mask: FrameMask | undefined, height: number) {
  return (["top", "bottom"] as const).flatMap((edge) => {
    const bar = mask?.[edge];
    const size = bar?.enabled ? Math.round(height * bar.height / 100) : 0;
    return size > 0 ? [{ edge, y: edge === "top" ? 0 : height - size, height: size, color: bar!.color, gradient: bar!.gradient }] : [];
  });
}

/** 生成透明背景 PNG；栏内按 sRGB 通道逐行插值并四舍五入，与后端 Pillow 保持相同像素。 */
export function frameMaskImage(mask: FrameMask | undefined, width: number, height: number): string | undefined {
  const rects = frameMaskRects(mask, height);
  if (!rects.length) return undefined;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("无法生成蒙版预览");
  for (const rect of rects) {
    const gradient = rect.gradient;
    if (!gradient?.enabled) {
      context.fillStyle = rect.color;
      context.fillRect(0, rect.y, width, rect.height);
      continue;
    }
    const start = [1, 3, 5].map((i) => parseInt(rect.color.slice(i, i + 2), 16));
    const end = [1, 3, 5].map((i) => parseInt(gradient.endColor.slice(i, i + 2), 16));
    for (let row = 0; row < rect.height; row++) {
      const distance = gradient.direction === "down" ? row : rect.height - 1 - row;
      const t = Math.min(1, distance / (Math.max(1, rect.height - 1) * gradient.range / 100));
      context.fillStyle = `rgb(${start.map((value, i) => Math.round(value + (end[i] - value) * t)).join(",")})`;
      context.fillRect(0, rect.y + row, width, 1);
    }
  }
  return canvas.toDataURL("image/png");
}
