/** 字体信息的中文展示：来源、字形名称、字重与文件大小；字体库卡片与字体详情共用。 */
import type { FontFaceInfo, FontInfo } from "./api";

/** 字体来源的中文说明。 */
export const SOURCES: Record<FontInfo["source"], string> = { system: "系统默认", builtin: "预装", upload: "用户上传" };

/** 字形的中文名称。 */
export const SLOT_NAMES: Record<FontFaceInfo["slot"], string> = { regular: "常规", bold: "粗体", italic: "斜体", bold_italic: "粗斜体" };

/** 一个字形的字重：单一字重写数字，可变字体或系统字体写覆盖的范围。 */
export const weightText = (weights: number[]) => weights.join("–");

/** 文件大小的简短显示：不足 1 MB 按 KB；系统默认字体没有文件。 */
export const sizeText = (size: number) =>
  size <= 0 ? "随系统安装" : size < 1024 * 1024 ? `${Math.ceil(size / 1024)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
