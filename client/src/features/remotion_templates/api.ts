/** Remotion HTTP 客户端；请求有超时，写入不自动重试，每个请求携带本机设备 ID，模型凭据只随模型任务与能力查询发送。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import type {
  Composition,
  DiagnosticsReport,
  Job,
  Values,
  Version,
  WorkEvent,
} from "./model";
import { deviceId } from "./localSessions";
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

/** 503 的 detail 是服务端自己编写的就绪原因（缺沙箱、未配置模型等）；缺失或不是文本时回退到通用提示。 */
async function serviceDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    return typeof detail === "string" && detail.trim() ? detail : null;
  } catch {
    return null;
  }
}

/** 读取 JSON 或代码文本；中断与网络失败保留由调用方维护的草稿。 */
async function request<T>(
  path: string,
  options: RequestInit = {},
  text = false,
  timeoutMs = 20_000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, timeoutMs);
  try {
    const device = await deviceId();
    const response = await fetch(apiUrl(path), {
      ...options,
      signal: controller.signal,
      headers: {
        "X-IMV-Device": device,
        ...(typeof options.body === "string"
          ? { "Content-Type": "application/json" }
          : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      const detail = response.status === 503 ? await serviceDetail(response) : null;
      throw new ApiError(
        response.status,
        detail ??
          (response.status === 409
            ? "当前任务状态已变化，请刷新任务后重试。"
            : response.status === 413
              ? "图片超过 10 MiB，请选择较小的图片。"
              : response.status === 422
                ? "输入未通过校验，请检查文字、图片或参数。"
                : response.status === 503
                  ? "服务尚未就绪，请检查服务端配置。"
                  : `请求未完成（${response.status}），请重试。`),
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
/** 首次发送才创建作品；尚未收到 ID 的请求不自动重试。「关键词字效」「双栏」「双语字幕」只在打开时携带，关闭时沿用服务端默认值。 */
export function create(
  description: string,
  assets: string[] = [],
  composition?: Composition,
  switches: { keywordEffect?: boolean; twoTier?: boolean; bilingual?: boolean } = {},
): Promise<{
  work: { id: string; current_version_id: string | null; created_at: string; request: { description?: string | null } };
  job: Job;
}> {
  return modelRequest("/works", {
    method: "POST",
    body: JSON.stringify({
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(assets.length ? { images: assets.map((asset_id) => ({ asset_id })) } : {}),
      ...(composition ? { composition } : {}),
      ...(switches.keywordEffect ? { keyword_effect: true } : {}),
      ...(switches.twoTier ? { two_tier: true } : {}),
      ...(switches.bilingual ? { bilingual: true } : {}),
    }),
  });
}
/** 自然语言修改（文字和／或参考图）、澄清和参数共用同一端点，显式绑定成功版本或问题任务。 */
export function message(
  work: string,
  body: {
    instruction?: string;
    images?: { asset_id: string }[];
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
/** 读取服务端对已验收版本重新执行的隔离类型检查；失败不影响代码展示。 */
export function diagnostics(
  id: string,
  signal?: AbortSignal,
): Promise<DiagnosticsReport> {
  // Server render_timeout_seconds is capped at 600; allow transport/cleanup overhead.
  return request(`/versions/${encodeURIComponent(id)}/diagnostics`, { signal }, false, 630_000);
}
/** 恢复当前作品的全部成功版本；内部候选不会进入该接口。 */
export function versions(
  work: string,
  signal?: AbortSignal,
): Promise<Version[]> {
  return request(`/works/${encodeURIComponent(work)}/versions`, { signal });
}
/** 读取任务当前状态；本机还停在运行中而服务端已无事件可送时，用它校正本地状态。 */
export function job(id: string, signal?: AbortSignal): Promise<Job> {
  return request(`/jobs/${encodeURIComponent(id)}`, { signal });
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

/** 读取一页编号大于 after 的待送达公开事件（升序，可能少于全部）；后台同步据此补齐未在 SSE 中打开的会话。 */
export function workEvents(
  work: string,
  after: number,
  signal?: AbortSignal,
): Promise<WorkEvent[]> {
  return request(`/works/${encodeURIComponent(work)}/events?after=${after}`, {
    signal,
  });
}

/** 本地写入事件后确认送达；服务端删除该编号及之前的事件，不影响任务。 */
export function ack(work: string, cursor: number): Promise<void> {
  return request(`/works/${encodeURIComponent(work)}/ack`, {
    method: "POST",
    body: JSON.stringify({ cursor }),
  });
}

/**
 * 删除要等服务端停止任务、写完进行中的保存并逐个版本清理对象存储，远程部署时可能远超普通请求的 20 秒；
 * 超时只结束本机等待，服务端照常清理，重试删除会等待同一次清理。
 */
export const DELETE_TIMEOUT_MS = 120_000;

/** 整条删除由服务端停止任务并清理数据；写入失败不自动重发。 */
export function deleteWork(id: string): Promise<void> {
  return request(`/works/${encodeURIComponent(id)}`, { method: "DELETE" }, false, DELETE_TIMEOUT_MS);
}
