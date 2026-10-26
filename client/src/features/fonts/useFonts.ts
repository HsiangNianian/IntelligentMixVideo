/** 字体库状态：读取共享字体库并提供上传；下拉选择器与字体库页面共用，卸载时取消读取。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/features/remotion_templates/api";
import { cardWithFile, fontDigest, listFonts, uploadFont, type FontInfo, type UploadedFont } from "./api";
import { fontDisplayName } from "./names";
import { drawFontPreview } from "./preview";
import { repairFont } from "./repair";

/** 浏览器画预览条的函数；测试里可替换。 */
export type Previewer = (file: File, text: string) => Promise<Blob | null>;

/** 一次上传的结果：服务端返回的卡片，以及上传前在浏览器里自动修复的项目（没有修复时为空）。 */
export type UploadResult = UploadedFont & { repairs: string[] };

/** 浏览器拒绝解码上传文件时的提示；`fixed` 是上传前已自动修复的项目，为空说明文件本来就没有可修的地方。 */
function undecodable(fixed: readonly string[]): string {
  return fixed.length
    ? `浏览器仍无法解码这个字体文件（已自动修复：${fixed.join("；")}）。请用字体工具重新导出后再试。`
    : "浏览器无法解码这个字体文件：文件可能已损坏，或不是标准的 TrueType / OpenType 字体。请用字体工具重新导出后再试。";
}

/**
 * 读取一次字体库；`fonts` 为 null 表示尚未读到。`upload` 先修复、再让浏览器画预览条（同时验证字体可读）后上传，
 * 成功后刷新字库并返回字体所在的卡片；失败返回 null，原因在 `problem`。读取失败的原因在 `loadError`，可用 `reload` 重试。
 */
export function useFonts() {
  const [fonts, setFonts] = useState<FontInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // 最近读到的字体库，上传时按文件摘要查找，不随渲染重建上传函数。
  const latest = useRef<FontInfo[] | null>(null);
  // 每次读取递增的序号：迟到的旧响应不能覆盖上传后读到的较新字体库。
  const loadSeq = useRef(0);
  useUpdateBlocker("字体正在上传", uploading);
  /** 读取字体库并返回列表；失败时记下原因并返回 null。只应用最后一次发出的读取结果。 */
  const load = useCallback((signal?: AbortSignal) => {
    const seq = ++loadSeq.current;
    return listFonts(signal).then((list) => {
      if (!signal?.aborted && seq === loadSeq.current) {
        latest.current = list;
        setFonts(list);
        setLoadError(null);
      }
      return list;
    }).catch((error: unknown) => {
      if (!signal?.aborted && seq === loadSeq.current) setLoadError(error instanceof Error ? error.message : "字体库读取失败，请重试。");
      return null;
    });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  /**
   * 上传一个字体文件。上传前先做目录级修复（见 `repair.ts`：删除零长度的表、重建表目录并重算 head 校验和），
   * 摘要、预览条与上传都用修复后的字节，库里按内容去重才不会与实际保存的文件对不上；结构损坏、修不了的文件直接拒绝并说明原因。
   * 显示名取字体自己的名称（优先中文名，见 `fontDisplayName`），不取文件名；画预览条同时验证浏览器能解码该字体；
   * 读不出名称时只做这项验证、不保留预览条，名称交给服务端决定。
   * 字体库里已有同一个文件（按 SHA-256）时不再上传，直接返回它的卡片；修复前的原文件已登记时也不再上传修复版，
   * 避免同一字体在共享库里出现两张卡片（现有条目保持原样）。请求失败不等于没存上：超时或断线时服务端可能已经登记，
   * 旧服务端对重复文件回答 422，所以失败后重新读取字体库，找到这个文件就按已在库里处理。
   */
  const upload = useCallback(async (file: File, previewer: Previewer = drawFontPreview): Promise<UploadResult | null> => {
    setProblem(null);
    setUploading(true);
    let digest: string | null = null;
    let repairs: string[] = [];
    try {
      const original = await file.arrayBuffer();
      const planned = repairFont(original);
      if (planned.kind === "damaged") {
        setProblem(`字体文件结构损坏（${planned.detail}），无法自动修复。请用字体工具重新导出后再试。`);
        return null;
      }
      repairs = planned.kind === "repaired" ? planned.repairs : [];
      const bytes = planned.kind === "repaired" ? planned.bytes : original;
      const candidate = planned.kind === "repaired"
        ? new File([bytes], file.name, { type: file.type, lastModified: file.lastModified })
        : file;
      digest = await fontDigest(bytes);
      const known = digest && latest.current ? cardWithFile(latest.current, digest) : undefined;
      if (known) return { font: known, created: false, repairs };
      // 修复前的原文件可能已经登记（旧客户端、宽松引擎或直接调用 API）。命中它的摘要时不再上传修复版，
      // 以免同一字体在共享库里出现两张卡片；现有条目保持原样，服务端没有替换文件的接口。
      const originalDigest = planned.kind === "repaired" && digest ? await fontDigest(original) : digest;
      const originalKnown = originalDigest && originalDigest !== digest && latest.current
        ? cardWithFile(latest.current, originalDigest)
        : undefined;
      if (originalKnown) return { font: originalKnown, created: false, repairs: [] };
      const name = fontDisplayName(bytes);
      let strip: Blob | null = null;
      try {
        const drawn = await previewer(candidate, name ?? "");
        if (name) strip = drawn;
      } catch {
        setProblem(undecodable(repairs));
        return null;
      }
      const uploaded = await uploadFont(candidate, name ?? "", strip);
      await load();
      return { ...uploaded, repairs };
    } catch (error) {
      // 只有请求可能已经在服务端完成时才回读字体库：超时、断线、5xx，或旧服务端对重复文件回答的 422。
      // 413 与无效字体的 422 已确定没有登记，再读一次只会拖慢真正的错误提示。
      const maybeStored = !(error instanceof ApiError)
        || error.status >= 500
        || (error.status === 422 && error.message.includes("已经在字体库里"));
      const stored = digest && maybeStored ? await load().then((list) => list && cardWithFile(list, digest!)) : undefined;
      if (stored) return { font: stored, created: false, repairs };
      setProblem(error instanceof Error ? error.message : "上传失败，请重试。");
      return null;
    } finally {
      setUploading(false);
    }
  }, [load]);
  return { fonts, loadError, problem, uploading, upload, reload: () => load() };
}
