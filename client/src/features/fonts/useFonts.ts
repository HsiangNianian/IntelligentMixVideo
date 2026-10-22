/** 字体库状态：读取共享字体库并提供上传；下拉选择器与字体库页面共用，卸载时取消读取。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/features/remotion_templates/api";
import { cardWithFile, fontDigest, listFonts, uploadFont, type FontInfo, type UploadedFont } from "./api";
import { fontDisplayName } from "./names";
import { drawFontPreview } from "./preview";

/** 浏览器画预览条的函数；测试里可替换。 */
export type Previewer = (file: File, text: string) => Promise<Blob | null>;

/**
 * 读取一次字体库；`fonts` 为 null 表示尚未读到。`upload` 先让浏览器画预览条（同时验证字体可读）再上传，
 * 成功后刷新字库并返回字体所在的卡片；失败返回 null，原因在 `problem`。读取失败的原因在 `loadError`，可用 `reload` 重试。
 */
export function useFonts() {
  const [fonts, setFonts] = useState<FontInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // 最近读到的字体库，上传时按文件摘要查找，不随渲染重建上传函数。
  const latest = useRef<FontInfo[] | null>(null);
  useUpdateBlocker("字体正在上传", uploading);
  /** 读取字体库并返回列表；失败时记下原因并返回 null。 */
  const load = useCallback((signal?: AbortSignal) => listFonts(signal).then((list) => {
    latest.current = list;
    setFonts(list);
    setLoadError(null);
    return list;
  }).catch((error: unknown) => {
    if (!signal?.aborted) setLoadError(error instanceof Error ? error.message : "字体库读取失败，请重试。");
    return null;
  }), []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  /**
   * 上传一个字体文件。显示名取字体自己的名称（优先中文名，见 `fontDisplayName`），不取文件名；
   * 画预览条同时验证浏览器能解码该字体；读不出名称时只做这项验证、不保留预览条，名称交给服务端决定。
   * 字体库里已有同一个文件（按 SHA-256）时不再上传，直接返回它的卡片。请求失败不等于没存上：超时或断线时服务端
   * 可能已经登记，旧服务端对重复文件回答 422，所以失败后重新读取字体库，找到这个文件就按已在库里处理。
   */
  const upload = useCallback(async (file: File, previewer: Previewer = drawFontPreview): Promise<UploadedFont | null> => {
    setProblem(null);
    setUploading(true);
    let digest: string | null = null;
    try {
      const bytes = await file.arrayBuffer();
      digest = await fontDigest(bytes);
      const known = digest && latest.current ? cardWithFile(latest.current, digest) : undefined;
      if (known) return { font: known, created: false };
      const name = fontDisplayName(bytes);
      let strip: Blob | null = null;
      try {
        const drawn = await previewer(file, name ?? "");
        if (name) strip = drawn;
      } catch {
        setProblem("无法读取这个字体文件，请选择有效的 .ttf 或 .otf 字体。");
        return null;
      }
      const uploaded = await uploadFont(file, name ?? "", strip);
      await load();
      return uploaded;
    } catch (error) {
      // 只有请求可能已经在服务端完成时才回读字体库：超时、断线、5xx，或旧服务端对重复文件回答的 422。
      // 413 与无效字体的 422 已确定没有登记，再读一次只会拖慢真正的错误提示。
      const maybeStored = !(error instanceof ApiError)
        || error.status >= 500
        || (error.status === 422 && error.message.includes("已经在字体库里"));
      const stored = digest && maybeStored ? await load().then((list) => list && cardWithFile(list, digest!)) : undefined;
      if (stored) return { font: stored, created: false };
      setProblem(error instanceof Error ? error.message : "上传失败，请重试。");
      return null;
    } finally {
      setUploading(false);
    }
  }, [load]);
  return { fonts, loadError, problem, uploading, upload, reload: () => load() };
}
