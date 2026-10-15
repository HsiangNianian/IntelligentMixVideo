/** 字体库 HTTP 客户端：读取共享字体库、上传字体；上传失败直接展示服务端给出的原因（重名、格式、大小）。 */
import { ApiError, apiUrl } from "@/features/remotion_templates/api";
import { deviceId } from "@/features/remotion_templates/localSessions";
import { apiBase } from "@/lib/api-base";

/** 字体库里的一个字体；`family` 就是效果 `fontFamily` 参数的取值。 */
export interface FontInfo {
  id: string;
  family: string;
  label: string;
  style: string;
  weights: number[];
  /** system 是系统默认字体，builtin 是预装字体，upload 是用户上传的字体。 */
  source: "system" | "builtin" | "upload";
  size: number;
  file_url: string | null;
  preview_url: string | null;
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

/** 带超时与设备头的请求；失败时抛出带公共提示的 ApiError，中断原样抛出。 */
async function request<T>(path: string, init: RequestInit, timeoutMs: number): Promise<T> {
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
    return (await response.json()) as T;
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
  return request("/fonts", { signal }, 20_000);
}

/** 上传一个字体文件；`preview` 是用该字体画出的黑色透明底 PNG 预览条，下拉框用它展示字体。写入不自动重试。 */
export function uploadFont(file: File, label: string, preview: Blob | null, signal?: AbortSignal): Promise<FontInfo> {
  const form = new FormData();
  form.append("file", file);
  form.append("label", label);
  if (preview) form.append("preview", preview, "preview.png");
  return request("/fonts", { method: "POST", body: form, signal }, 120_000);
}

/** 预览条的完整地址；没有预览条的字体返回 null，界面改用文字名称。 */
export function previewUrl(font: FontInfo): string | null {
  return font.preview_url ? apiBase() + font.preview_url : null;
}
