/** 在真实浏览器中挂载模板及 Remotion 字体组件，使用 React 状态记录选择结果；不替换网络或字体接口。 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TemplateFontPicker } from "@/features/templates/TemplateFontPicker";
import { FontPicker } from "@/features/fonts/FontPicker";
import "@/styles/globals.css";

/** 两个 IMS 字体独立更新，Remotion 从真实服务端读取字体并提供上传。 */
function FontSelectTest() {
  const [title, setTitle] = useState("Alibaba PuHuiTi");
  const [subtitle, setSubtitle] = useState("Alibaba PuHuiTi");
  const [remotion, setRemotion] = useState("Noto Sans CJK SC");
  return <main className="mx-auto grid max-w-sm gap-6 p-8">
    <label htmlFor="title-font">标题字体</label>
    <TemplateFontPicker id="title-font" value={title} onChange={setTitle} />
    <output aria-label="标题字体值">{title}</output>
    <label htmlFor="subtitle-font">字幕字体</label>
    <TemplateFontPicker id="subtitle-font" value={subtitle} onChange={setSubtitle} />
    <output aria-label="字幕字体值">{subtitle}</output>
    <FontPicker id="remotion-font" label="Remotion 字体" value={remotion} onChange={setRemotion} />
    <output aria-label="Remotion 字体值">{remotion}</output>
  </main>;
}

createRoot(document.getElementById("root")!).render(<FontSelectTest />);
