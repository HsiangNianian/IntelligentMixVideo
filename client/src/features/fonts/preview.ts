/** 字体预览条：用上传者浏览器里的字体把名称画成黑色透明底 PNG，服务端只保留其透明度，页面用 CSS mask 着色。 */

/** 预览条画布：宽高与服务端 `PREVIEW_MAX` 一致（640×96 以内），文字黑色、背景透明。 */
const STRIP = { width: 640, height: 64, size: 44 } as const;
/** 上传的字体在画布上临时使用的家族名，画完即移除。 */
const STRIP_FAMILY = "imv-upload-preview";

/**
 * 画出预览条 PNG。浏览器无法解码该字体文件时抛出错误（同时是上传前的有效性检查）；
 * 没有画布能力（如测试环境）时返回 null，字体仍可上传，只是没有预览条。
 */
export async function drawFontPreview(file: File, text: string): Promise<Blob | null> {
  const face = new FontFace(STRIP_FAMILY, await file.arrayBuffer());
  document.fonts.add(await face.load());
  try {
    const canvas = document.createElement("canvas");
    canvas.width = STRIP.width;
    canvas.height = STRIP.height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.font = `${STRIP.size}px "${STRIP_FAMILY}"`;
    context.fillStyle = "#000";
    context.textBaseline = "alphabetic";
    context.fillText(text, 8, 48, STRIP.width - 16);
    return await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  } finally {
    document.fonts.delete(face);
  }
}
