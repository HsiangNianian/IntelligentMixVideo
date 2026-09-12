/** Remotion HTTP 客户端；请求有超时，写入不自动重试，服务密钥始终留在服务端。 */
import type { Job, Values, Version } from "./model";

const base =
  (import.meta.env.VITE_API_URL?.trim() || "http://localhost:8000").replace(
    /\/+$/,
    "",
  ) + "/api/templates";

/** 拼接当前服务内的已知路径，避免将下载请求发送到响应提供的其他来源。 */
export function apiUrl(path: string): string {
  return base + path;
}

/** 读取 JSON 或代码文本；中断与网络失败保留由调用方维护的草稿。 */
async function request<T>(
  path: string,
  options: RequestInit = {},
  text = false,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 20_000);
  try {
    const response = await fetch(apiUrl(path), {
      ...options,
      signal: controller.signal,
      headers:
        typeof options.body === "string"
          ? { "Content-Type": "application/json" }
          : undefined,
    });
    if (!response.ok) {
      throw new Error(
        response.status === 409
          ? "当前任务状态已变化，请刷新任务后重试。"
          : response.status === 413
            ? "图片超过 10 MiB，请选择较小的图片。"
            : response.status === 422
              ? "输入未通过校验，请检查文字、图片或参数。"
              : response.status === 503
                ? "服务尚未就绪，请检查服务端配置。"
                : `请求未完成（${response.status}），请重试。`,
      );
    }
    return (text ? await response.text() : await response.json()) as T;
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (controller.signal.aborted)
      throw new Error("请求超时，请检查任务状态后重试。");
    if (error instanceof TypeError)
      throw new Error("无法连接服务端，请确认服务已启动。");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
}

/** 能力查询不携带凭据，供空白会话显示服务就绪状态。 */
export function capabilities(
  signal?: AbortSignal,
): Promise<{ models_configured: boolean }> {
  return request("/capabilities", { signal });
}
/** 上传参考图片；浏览器负责 multipart 边界，图片不作为背景视频。 */
export function upload(
  image: File,
  signal?: AbortSignal,
): Promise<{ id: string }> {
  const form = new FormData();
  form.append("file", image);
  return request("/assets", { method: "POST", body: form, signal });
}
/** 首次发送才创建作品；尚未收到 ID 的请求不自动重试。 */
export function create(
  description: string,
  asset?: string,
): Promise<{ work: { id: string }; job: Job }> {
  return request("/works", {
    method: "POST",
    body: JSON.stringify({
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(asset ? { image: { asset_id: asset } } : {}),
    }),
  });
}
/** 自然语言修改、澄清和参数共用同一端点，显式绑定成功版本或问题任务。 */
export function message(
  work: string,
  body: {
    instruction?: string;
    parameters?: Values;
    base_version_id?: string;
    reply_to_job_id?: string;
  },
): Promise<Job> {
  return request(`/works/${encodeURIComponent(work)}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
/** 有界读取最新任务，轮询由会话协调且在卸载时取消。 */
export function job(id: string, signal?: AbortSignal): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(id)}`, { signal });
}
/** 只读取已验收版本。 */
export function version(id: string, signal?: AbortSignal): Promise<Version> {
  return request(`/versions/${encodeURIComponent(id)}`, { signal });
}
/** 复制与代码浮板使用同一份带默认参数的服务端导出。 */
export function exported(id: string, signal?: AbortSignal): Promise<string> {
  return request(
    `/versions/${encodeURIComponent(id)}/artifacts/Export.tsx`,
    { signal },
    true,
  );
}
/** 取消可重复调用；不删除之前的成功版本。 */
export function cancel(id: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
/** 由用户明确重试已结束任务，避免网络故障产生重复生成。 */
export function retry(id: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
}
