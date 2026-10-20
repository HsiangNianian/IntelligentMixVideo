/** 字体库状态：读取共享字体库并提供上传；下拉选择器与字体库页面共用，卸载时取消读取。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { useCallback, useEffect, useState } from "react";
import { listFonts, uploadFont, type FontInfo } from "./api";
import { fontDisplayName } from "./names";
import { drawFontPreview } from "./preview";

/** 浏览器画预览条的函数；测试里可替换。 */
export type Previewer = (file: File, text: string) => Promise<Blob | null>;

/**
 * 读取一次字体库；`fonts` 为 null 表示尚未读到。`upload` 先让浏览器画预览条（同时验证字体可读）再上传，
 * 成功后刷新字库并返回新字体；失败返回 null，原因在 `problem`。读取失败的原因在 `loadError`，可用 `reload` 重试。
 */
export function useFonts() {
  const [fonts, setFonts] = useState<FontInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  useUpdateBlocker("字体正在上传", uploading);
  const load = useCallback((signal?: AbortSignal) => listFonts(signal).then((list) => {
    setFonts(list);
    setLoadError(null);
  }).catch((error: unknown) => {
    if (!signal?.aborted) setLoadError(error instanceof Error ? error.message : "字体库读取失败，请重试。");
  }), []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  /**
   * 上传一个字体文件。显示名取字体自己的名称（优先中文名，见 `fontDisplayName`），不取文件名；
   * 画预览条同时验证浏览器能解码该字体；读不出名称时只做这项验证、不保留预览条，名称交给服务端决定。
   */
  const upload = useCallback(async (file: File, previewer: Previewer = drawFontPreview): Promise<FontInfo | null> => {
    setProblem(null);
    setUploading(true);
    try {
      const name = fontDisplayName(await file.arrayBuffer());
      let strip: Blob | null = null;
      try {
        const drawn = await previewer(file, name ?? "");
        if (name) strip = drawn;
      } catch {
        setProblem("无法读取这个字体文件，请选择有效的 .ttf 或 .otf 字体。");
        return null;
      }
      const created = await uploadFont(file, name ?? "", strip);
      await load();
      return created;
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "上传失败，请重试。");
      return null;
    } finally {
      setUploading(false);
    }
  }, [load]);
  return { fonts, loadError, problem, uploading, upload, reload: () => load() };
}
