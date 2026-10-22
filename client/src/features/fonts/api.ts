/** 字体库 HTTP 客户端：读取共享字体库、上传字体（按内容幂等）；上传失败直接展示服务端给出的原因（格式、大小、存储不可用）。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { ApiError, apiUrl } from "@/features/remotion_templates/api";
import { deviceId } from "@/features/remotion_templates/localSessions";
import { apiBase } from "@/lib/api-base";

/** 字体卡片的四个字形，按展示顺序。 */
export const FONT_SLOTS = ["regular", "bold", "italic", "bold_italic"] as const;

/** 字体卡片里的一个字形：一个字体文件及它的预览条；系统默认字体的文件是服务端安装的常规字重，本机没有时为空。 */
export interface FontFaceInfo {
  id: string;
  slot: (typeof FONT_SLOTS)[number];
  weights: number[];
  size: number;
  sha256: string;
  /** 该字形的字体文件；系统默认字体在本机未安装时为 null，不能打开字样。 */
  file_url: string | null;
  preview_url: string | null;
}

/**
 * 字体库里的一张字体卡片：一个家族，含常规、粗体、斜体、粗斜体中已有的字形（`faces`，按这个顺序，可以缺）；
 * `family` 就是效果 `fontFamily` 参数的取值，渲染时按字重与斜体选用对应的文件。
 */
export interface FontInfo {
  id: string;
  family: string;
  label: string;
  style: string;
  /** 各字形字重的合集。 */
  weights: number[];
  /** system 是系统默认字体，builtin 是预装字体，upload 是用户上传的字体。 */
  source: "system" | "builtin" | "upload";
  /** 预装字体的许可证（如 OFL-1.1），其他为空。 */
  license: string;
  /** 各字形文件大小之和。 */
  size: number;
  file_url: string | null;
  /** 卡片的预览条：第一个有预览条的字形。 */
  preview_url: string | null;
  faces: FontFaceInfo[];
}

/** 服务端发来的字体卡片；早于字体卡片的服务端（0.9.8 及以前）没有 `faces` 和 `license`。 */
type FontCard = Omit<FontInfo, "faces" | "license"> & Partial<Pick<FontInfo, "faces" | "license">>;

/**
 * 补齐早期服务端缺少的字段：一个字体就是只有常规字形的卡片。客户端热更新可能先于服务端部署，
 * 缺字段不能让字体库页面渲染时抛错（整个界面会变成黑屏）。
 */
function asCard(card: FontCard): FontInfo {
  const { id, weights, size, file_url, preview_url } = card;
  return { license: "", faces: [{ id, slot: "regular", weights, size, sha256: "", file_url, preview_url }], ...card };
}

/** 服务端 4xx/5xx 响应里的文字说明；不是文字时返回 null。 */
async function detailOf(response: Response): Promise<string | null> {
  try {
    const detail = ((await response.json()) as { detail?: unknown } | null)?.detail;
    return typeof detail === "string" && detail.trim() ? detail : null;
  } catch {
    return null;
  }
}

/** 带超时与设备头的请求，返回解析后的 JSON 与状态码；失败时抛出带公共提示的 ApiError，中断原样抛出。 */
async function request<T>(path: string, init: RequestInit, timeoutMs: number): Promise<{ status: number; body: T }> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  const timeout = window.setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      signal: controller.signal,
      headers: { "X-IMV-Device": await deviceId(), ...init.headers },
    });
    if (!response.ok)
      throw new ApiError(
        response.status,
        (await detailOf(response)) ?? (response.status === 413 ? "字体文件过大。" : `请求未完成（${response.status}），请重试。`),
      );
    return { status: response.status, body: (await response.json()) as T };
  } catch (error) {
    if (init.signal?.aborted || error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new Error("请求超时，请重试。");
    if (error instanceof TypeError) throw new Error("无法连接服务端，请确认服务已启动。");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abort);
  }
}

/** 读取共享字体库（预装与用户上传），按登记顺序，默认字体在最前。 */
export function listFonts(signal?: AbortSignal): Promise<FontInfo[]> {
  return request<FontCard[]>("/fonts", { signal }, 20_000).then(({ body }) => body.map(asCard));
}

/** 一次上传的结果：文件所在的整张卡片，以及这个文件是这次新登记的（`created`）还是库里本来就有。 */
export interface UploadedFont {
  font: FontInfo;
  created: boolean;
}

/**
 * 上传一个字体文件；`preview` 是用该字体画出的黑色透明底 PNG 预览条，下拉框用它展示字体。
 * 返回文件所在的整张卡片（同一家族的常规、粗体、斜体、粗斜体文件归入同一张卡片）。上传按内容幂等：
 * 库里已有同一个文件时服务端回答 200 与它的卡片（新登记为 201），超时后重试不会被拒绝。写入不自动重试。
 */
export function uploadFont(file: File, label: string, preview: Blob | null, signal?: AbortSignal): Promise<UploadedFont> {
  const form = new FormData();
  form.append("file", file);
  form.append("label", label);
  if (preview) form.append("preview", preview, "preview.png");
  return request<FontCard>("/fonts", { method: "POST", body: form, signal }, 120_000).then(({ status, body }) => ({
    font: asCard(body),
    created: status === 201,
  }));
}

/**
 * 字体文件内容的 SHA-256（十六进制），与字形的 `sha256` 对应；不在安全上下文（HTTP 部署的网页）时浏览器不提供摘要，
 * 返回 null。摘要只是上传前的优化，计算失败也不能挡住上传：服务端按内容幂等，所以同样返回 null 走服务端。
 */
export async function fontDigest(bytes: ArrayBuffer): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  try {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

/** 字体库里含有这个文件（按 SHA-256）的卡片；早期服务端的字形没有摘要，不会误配。 */
export function cardWithFile(fonts: readonly FontInfo[], digest: string): FontInfo | undefined {
  return fonts.find((font) => font.faces.some((face) => face.sha256 === digest));
}

/** 卡片或字形预览条的完整地址；没有预览条时返回 null，界面改用文字名称。 */
export function previewUrl(item: Pick<FontInfo, "preview_url">): string | null {
  return item.preview_url ? apiBase() + item.preview_url : null;
}
