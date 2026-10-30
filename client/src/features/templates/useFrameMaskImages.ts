/** 预览只在启用图片的引用变化时读取和解码；调高复用图片，切换与卸载取消读取并释放临时 URL。 */
import { useEffect, useState } from "react";
import { readFrameMaskImage } from "./api";
import { frameMaskRects, type FrameMask } from "./frameMask";

/** 同时供 SDK 与基础预览绘制使用；读取失败显式返回错误，不能以原颜色代替图片。 */
export function useFrameMaskImages(mask: FrameMask | undefined, height: number, attempt: number) {
  const key = [...new Set(frameMaskRects(mask, height).flatMap((rect) => rect.imageId ? [rect.imageId] : []))].sort().join(",");
  const [state, setState] = useState({ key: "", images: new Map<string, HTMLImageElement>(), error: "" });
  useEffect(() => {
    if (!key) {
      setState((previous) => previous.key ? { key, images: new Map(), error: "" } : previous);
      return;
    }
    const controller = new AbortController();
    setState({ key, images: new Map(), error: "" });
    void Promise.all(key.split(",").map(async (id): Promise<[string, HTMLImageElement]> => {
      const blob = await readFrameMaskImage(id, controller.signal);
      const url = URL.createObjectURL(blob);
      try {
        const image = new Image();
        image.src = url;
        await image.decode();
        return [id, image];
      } finally { URL.revokeObjectURL(url); }
    })).then((entries) => {
      if (!controller.signal.aborted) setState({ key, images: new Map(entries), error: "" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ key, images: new Map(), error: `蒙版图片读取失败：${error instanceof Error ? error.message : "请重试预览"}` });
    });
    return () => controller.abort();
  }, [key, attempt]);
  return { images: state.images, ready: !key || (state.key === key && key.split(",").every((id) => state.images.has(id))), error: state.key === key ? state.error : "" };
}
