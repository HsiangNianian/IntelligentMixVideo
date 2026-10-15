/** 标题和字幕共用的字体目录；模板保存 IMS 标识，生成预览时转换为 SDK 标识。 */
import fonts from "../../../../server/src/server/template/fonts.json";

export const templateFonts = fonts;

/** 校验模板字体并返回对应的预览 SDK 标识，拒绝目录以外的字体。 */
export function previewFont(value: string): string {
  const font = templateFonts.find((item) => item.value === value);
  if (!font) throw new Error("字体不在支持的目录中");
  return font.preview;
}
