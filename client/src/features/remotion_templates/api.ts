/** Remotion HTTP 客户端；请求有超时，写入不自动重试，模型凭据从客户端读取并仅随模型任务与能力查询发送。 */
import type {
  Composition,
  Job,
  SessionSnapshot,
  Values,
  Version,
  WorkPage,
} from "./model";
import { readSettings } from "@/features/settings/api";
import { apiBase } from "@/lib/api-base";

/** 拼接当前服务内的已知路径，避免将下载请求发送到响应提供的其他来源。 */
export function apiUrl(path: string): string {
  return apiBase() + "/api/templates" + path;
}

/** 保留 HTTP 状态供会话区分永久删除与暂时断线，不暴露后端内部错误。 */
export class ApiError extends Error {
  /** 状态用于恢复决策，message 是可直接展示的公共提示。 */
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
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
      headers: {
        ...(typeof options.body === "string"
          ? { "Content-Type": "application/json" }
          : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw new ApiError(
        response.status,
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
    if (response.status === 204) return undefined as T;
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

/** 每次模型操作读取已保存快照；头部不会进入作品、聊天或任务输入的持久化。 */
async function modelRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const config = (await readSettings()).remotion_agent;
  return request(path, {
    ...options,
    headers: config
      ? { "X-Remotion-Config": encodeURIComponent(JSON.stringify(config)) }
      : undefined,
  });
}

/** 按当前客户端模型配置查询就绪状态，省略配置时使用服务端默认值。 */
export function capabilities(
  signal?: AbortSignal,
): Promise<{ models_configured: boolean }> {
  return modelRequest("/capabilities", { signal });
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
  composition?: Composition,
): Promise<{ work: { id: string }; job: Job }> {
  return modelRequest("/works", {
    method: "POST",
    body: JSON.stringify({
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(asset ? { image: { asset_id: asset } } : {}),
      ...(composition ? { composition } : {}),
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
  return modelRequest(`/works/${encodeURIComponent(work)}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
/** 只读取已验收版本。 */
export function version(id: string, signal?: AbortSignal): Promise<Version> {
  return request(`/versions/${encodeURIComponent(id)}`, { signal });
}
/** 聊天版本卡片复制对应成功版本的完整默认参数导出。 */
export function exported(id: string, signal?: AbortSignal): Promise<string> {
  return request(
    `/versions/${encodeURIComponent(id)}/artifacts/Export.tsx`,
    { signal },
    true,
  );
}
/** 恢复当前作品的全部成功版本；内部候选不会进入该接口。 */
export function versions(
  work: string,
  signal?: AbortSignal,
): Promise<Version[]> {
  return request(`/works/${encodeURIComponent(work)}/versions`, { signal });
}
/** 取消可重复调用；不删除之前的成功版本。 */
export function cancel(id: string): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
/** 由用户明确重试已结束任务，避免网络故障产生重复生成。 */
export function retry(id: string): Promise<Job> {
  return modelRequest(`/jobs/${encodeURIComponent(id)}/retry`, {
    method: "POST",
  });
}

/** 获取历史列表；每页只含轻量会话元数据。 */
export function history(
  cursor?: string,
  signal?: AbortSignal,
): Promise<WorkPage> {
  return request(
    `/works?history=true${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    { signal },
  );
}
/** 恢复公开历史及订阅游标；before 仅用于加载更早消息。 */
export function session(
  id: string,
  signal?: AbortSignal,
  before?: number,
): Promise<SessionSnapshot> {
  return request(
    `/works/${encodeURIComponent(id)}/session${before ? `?before=${before}` : ""}`,
    { signal },
  );
}

/** 整条删除由服务端停止任务并清理数据；写入失败不自动重发。 */
export function deleteWork(id: string): Promise<void> {
  return request(`/works/${encodeURIComponent(id)}`, { method: "DELETE" });
}
