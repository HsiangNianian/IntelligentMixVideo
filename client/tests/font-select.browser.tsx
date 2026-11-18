/** 在真实浏览器中挂载模板及 Remotion 字体组件，使用 React 状态记录选择结果；默认使用真实目录；?library 使用固定目录复现字体库滚动，不依赖后端。 */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TemplateFontPicker } from "@/features/templates/TemplateFontPicker";
import { FontPicker } from "@/features/fonts/FontPicker";
import { FontLibrary } from "@/features/fonts/FontLibrary";
import { loadFontFile } from "@/features/fonts/FontSpecimen";
import type { FontInfo } from "@/features/fonts/api";
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

// 30 个家族足以触发列表内部滚动；本地字体用于真实 FontFace 加载，避免连接业务服务。
const libraryMode = new URLSearchParams(location.search).has("library");
if (libraryMode) {
  const fonts: FontInfo[] = Array.from({ length: 30 }, (_, index) => ({
    id: `fixture-${index}`, family: `Fixture ${index}`, label: `测试字体 ${index}`, style: "滚动回归样本",
    weights: [400], source: "builtin", license: "OFL-1.1", size: 1024, file_url: "/fonts/imv-hand.woff2", preview_url: null,
    faces: [{ id: `fixture-${index}`, slot: "regular", weights: [400], size: 1024, sha256: "", file_url: "/fonts/imv-hand.woff2", preview_url: null }],
  }));
  window.fetch = (async (input: RequestInfo | URL) => {
    if (new URL(String(input)).pathname === "/api/templates/fonts") return Response.json(fonts);
    throw new Error("字体库回归夹具不允许请求业务服务");
  }) as typeof fetch;
}
createRoot(document.getElementById("root")!).render(libraryMode
  ? <main className="h-dvh"><FontLibrary loader={() => loadFontFile("/fonts/imv-hand.woff2")} /></main>
  : <FontSelectTest />);
