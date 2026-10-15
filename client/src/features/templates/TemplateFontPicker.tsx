/** IMS 字体选择：固定十一种保存值，使用 SDK 对应字体绘制的随包预览条，与 Remotion 共用选择界面。 */
import { FontSelect, type FontOption } from "@/features/fonts/FontSelect";
import { templateFonts } from "./fonts";

/** Vite 将预览条打包为本地资源；每项使用自己的 SDK 字体名称图像。 */
const options: FontOption[] = templateFonts.map((font) => ({
  value: font.value, label: font.label, description: font.value, keywords: font.preview,
  previewUrl: new URL(`./font-previews/${font.value}.png`, import.meta.url).href,
}));

/** 选中后原样回传 IMS 标识；标题和字幕由各自的受控值保持独立。 */
export function TemplateFontPicker({ id, value, onChange }: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return <FontSelect id={id} label="字体" value={value} options={options} onChange={onChange} />;
}
