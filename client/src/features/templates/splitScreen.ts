/** 同屏布局的默认值、保存校验及预览几何；与服务端 template/split_screen.py 使用相同单位。 */
import { apiBase } from "@/lib/api-base";

export interface ScreenPoint { x: number; y: number }
export interface ScreenWindow extends ScreenPoint {
  width: number;
  height: number;
  fit: "Contain" | "Cover";
  radius: number;
  borderWidth: number;
  borderColor: string;
}
export interface SplitScreen {
  enabled: boolean;
  layout: "split" | "overlay" | "circle";
  backgroundColor: string;
  material: ScreenWindow;
  avatar: ScreenWindow;
  subtitle: ScreenPoint;
  subtitleEn: ScreenPoint;
}

/** 每份草稿持有独立对象；数字人来源仅是本机预览选择，不属于模板。 */
export function defaultSplitScreen(): SplitScreen {
  return {
    enabled: false, layout: "split", backgroundColor: "#000000",
    material: { x: 8, y: 12, width: 84, height: 48, fit: "Contain", radius: 0, borderWidth: 0, borderColor: "#FFFFFF" },
    avatar: { x: 35, y: 68, width: 30, height: 21, fit: "Contain", radius: 30, borderWidth: 6, borderColor: "#FFFFFF" },
    subtitle: { x: 50, y: 62 }, subtitleEn: { x: 50, y: 65.5 },
  };
}

/** 保存边界一次校验；编辑中的无效输入不替换当前有效布局。 */
export function validateSplitScreen(value: SplitScreen): void {
  const color = (text: string) => /^#[0-9a-f]{6}$/i.test(text);
  if (typeof value.enabled !== "boolean" || !color(value.backgroundColor)) throw new Error("同屏开关或背景色无效");
  if (!["split", "overlay", "circle"].includes(value.layout)) throw new Error("同屏布局类型无效");
  for (const point of [value.subtitle, value.subtitleEn, value.material, value.avatar])
    if (![point.x, point.y].every((n) => Number.isFinite(n) && n >= 0 && n <= 100)) throw new Error("同屏坐标必须在 0～100% 之间");
  for (const window of [value.material, value.avatar]) {
    if (![window.width, window.height].every((n) => Number.isFinite(n) && n > 0 && n <= 100)
      || window.x + window.width > 100 || window.y + window.height > 100) throw new Error("同屏窗口必须完整位于画布内");
    const limit = Math.min(window.width * 10.8, window.height * 19.2) / 2;
    if (![window.radius, window.borderWidth].every((n) => Number.isFinite(n) && n >= 0 && n <= limit)
      || !color(window.borderColor) || !["Contain", "Cover"].includes(window.fit)) throw new Error("同屏圆角、边框或显示方式无效");
  }
}

/** SDK 和基础播放器共用布局几何；圆形仅示意居中裁切，真实头部位置在合成时检测。 */
export function previewRect(layout: SplitScreen, role: "material" | "avatar", source: { width: number; height: number }) {
  if (role === "material" && layout.layout !== "split") return { x: 0, y: 0, width: 1080, height: 1920, radius: 0, border: 0 };
  if (role === "avatar" && layout.layout === "circle") {
    const rect = screenRect({ ...layout.avatar, fit: "Contain" }, { width: 1, height: 1 });
    return { ...rect, radius: rect.width / 2, border: 0 };
  }
  const rect = screenRect(layout.layout === "overlay" ? { ...layout[role], fit: "Contain" } : layout[role], source);
  return layout.layout === "overlay" ? { ...rect, radius: 0, border: 0 } : rect;
}

/** 完整显示按源比例居中缩进窗口；圆角和内边框围住实际可见画面，留白属于画布背景。 */
export function screenRect(window: ScreenWindow, source: { width: number; height: number }, canvas = { width: 1080, height: 1920 }) {
  let x = window.x * canvas.width / 100, y = window.y * canvas.height / 100;
  let width = window.width * canvas.width / 100, height = window.height * canvas.height / 100;
  if (window.fit === "Contain") {
    const scale = Math.min(width / source.width, height / source.height);
    x += (width - source.width * scale) / 2; y += (height - source.height * scale) / 2;
    width = source.width * scale; height = source.height * scale;
  }
  const factor = Math.min(canvas.width / 1080, canvas.height / 1920);
  const w = Math.max(2, Math.round(width)), h = Math.max(2, Math.round(height));
  return { x: Math.round(x), y: Math.round(y), width: w, height: h,
    radius: Math.min(window.radius * factor, w / 2, h / 2), border: Math.min(window.borderWidth * factor, w / 2, h / 2) };
}

/** 预览装饰图片与云端 PNG 使用相同的内描边几何；SVG 只含校验后的数值和颜色。 */
export function borderSvg(rect: ReturnType<typeof screenRect>, color: string): string {
  const { width: w, height: h, radius: r, border: b } = rect;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><mask id="ring"><rect width="${w}" height="${h}" rx="${r}" fill="white"/><rect x="${b}" y="${b}" width="${Math.max(0, w - 2 * b)}" height="${Math.max(0, h - 2 * b)}" rx="${Math.max(0, r - b)}" fill="black"/></mask></defs><rect width="${w}" height="${h}" fill="${color}" mask="url(#ring)"/></svg>`)}`;
}

/** 角色只影响布局示例，选中数字人的原片段用另一个视频展示素材，不重排预览总时长。 */
export function previewMaterial(source: number, avatar: number): number {
  return source === avatar ? (avatar + 1) % 3 : source;
}

/** SDK 5.2.2 用独立黑白视频做裁切；源时长只决定单帧的持续时间，不上传或重编码源视频。 */
export function previewMaskUrl(rect: ReturnType<typeof screenRect>, source: { width: number; height: number; duration: number }): string {
  const query = new URLSearchParams(Object.entries({ width: source.width, height: source.height,
    box_width: rect.width, box_height: rect.height, radius: rect.radius, seconds: Math.ceil(source.duration) + 1,
  }).map(([key, value]) => [key, String(value)]));
  return `${apiBase()}/template/preview-mask?${query}`;
}
