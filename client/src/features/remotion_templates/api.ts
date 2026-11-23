/** Remotion HTTP 客户端；请求有超时，写入不自动重试，每个请求携带本机设备 ID，模型凭据只随模型任务与能力查询发送。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { diagnosticTask, diagnosticTaskState, diagnosticContext, inDiagnosticContext } from "@/lib/diagnostics";
import type {
  Composition,
  ClientContext,
  DiagnosticsReport,
  Job,
  Values,
  Version,
  WorkEvent,
} from "./model";
import { context, decodeImage, deviceId, encodeImage, image as localImage, linkImages, saveContext, server, storage } from "./localSessions";
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

/** 读取服务端固定的就绪或窗口丢失说明；缺失或不是文本时回退到通用提示。 */
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
  const base = apiBase();
  return diagnosticTask("前端字效", `${options.method ?? "GET"} ${path}`, async (trace) => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    const timeout = window.setTimeout(abort, timeoutMs);
    try {
      const device = await deviceId();
      const response = await trace.run(() => fetch(`${base}/api/templates${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          "X-IMV-Device": device,
          ...(typeof options.body === "string"
            ? { "Content-Type": "application/json" }
            : {}),
          ...options.headers,
        },
      }));
      if (!response.ok) {
        const detail = response.status === 503 || (response.status === 409 && path.endsWith("/context"))
          ? await serviceDetail(response) : null;
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
      const result = (text ? await response.text() : await response.json()) as T;
      // 只记录任务机器字段；公开回复也可能包含用户文本，不能进入诊断。
      const record = result as { job?: Job; id?: string; project_id?: string; status?: string };
      const job = record?.job ?? (record?.project_id && record?.status ? record as Job : undefined);
      if (job) trace.run(() => diagnosticTaskState("字效", job.id, job.status, { service: base }));
      return result;
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
  });
}

/** 每次模型操作读取已保存快照；头部不会进入作品、聊天或任务输入的持久化。 */
async function modelRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return diagnosticTask("前端字效", "读取模型配置并请求", async (trace) => {
    const config = (await readSettings()).remotion_agent;
    return trace.run(() => request(path, {
      ...options,
      headers: config
        ? { "X-Remotion-Config": encodeURIComponent(JSON.stringify(config)) }
        : undefined,
    }));
  });
}

/** 按当前客户端模型配置查询就绪状态，省略配置时使用服务端默认值。 */
export function capabilities(
  signal?: AbortSignal,
): Promise<{ models_configured: boolean }> {
  return modelRequest("/capabilities", { signal });
}
/** 上传参考图片；浏览器负责 multipart 边界，图片不作为背景视频。 */
export async function upload(
  image: File,
  signal?: AbortSignal,
): Promise<{ id: string }> {
  const origin = diagnosticContext();
  const scope = server();
  const form = new FormData();
  form.append("file", image);
  const result = await request<{ id: string; data: string }>("/assets", { method: "POST", body: form, signal });
  await inDiagnosticContext(origin, () => storage().putImage(scope, result.id, decodeImage(result.data)));
  return { id: result.id };
}
/** 首次发送才创建作品；尚未收到 ID 的请求不自动重试。「关键词字效」「双栏」「双语字幕」只在打开时携带，关闭时沿用服务端默认值。 */
export async function create(
  description: string,
  assets: string[] = [],
  composition?: Composition,
  switches: { keywordEffect?: boolean; twoTier?: boolean; bilingual?: boolean } = {},
): Promise<{
  work: { id: string; current_version_id: string | null; created_at: string; request: { description?: string | null; images?: { asset_id: string }[] } };
  job: Job;
}> {
  const origin = diagnosticContext();
  const payload = {
    method: "POST",
    body: JSON.stringify({
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(assets.length ? { images: assets.map((asset_id) => ({ asset_id })), assets: await imagePayloads(assets) } : {}),
      ...(composition ? { composition } : {}),
      ...(switches.keywordEffect ? { keyword_effect: true } : {}),
      ...(switches.twoTier ? { two_tier: true } : {}),
      ...(switches.bilingual ? { bilingual: true } : {}),
    }),
  };
  return inDiagnosticContext(origin, () => modelRequest("/works", payload));
}
/** 自然语言修改（文字和／或参考图）、澄清和参数共用同一端点，显式绑定成功版本或问题任务。 */
export async function message(
  work: string,
  body: {
    instruction?: string;
    images?: { asset_id: string }[];
    parameters?: Values;
    base_version_id?: string;
    reply_to_job_id?: string;
  },
): Promise<Job> {
  const origin = diagnosticContext();
  const checkpoint = await modelContext(work);
  const added = (body.images ?? []).map((image) => image.asset_id);
  if (added.length) await inDiagnosticContext(origin, () => linkImages(work, added));
  const ids = [...(checkpoint.image_ids ?? []), ...added];
  const payload = {
    method: "POST",
    body: JSON.stringify({ ...body, context: checkpoint, ...(body.parameters || !ids.length ? {} : { assets: await inDiagnosticContext(origin, () => imagePayloads(ids)) }) }),
  };
  return inDiagnosticContext(origin, () => modelRequest(`/works/${encodeURIComponent(work)}/messages`, payload));
}
/** 优先原样复用本地窗口；首次迁移或终态回执未到时补取，落盘成功后才发送。 */
async function modelContext(work: string): Promise<ClientContext> {
  const origin = diagnosticContext();
  const saved = await context(work);
  if (saved) return saved;
  const checkpoint = await inDiagnosticContext(origin, () => request<ClientContext>(`/works/${encodeURIComponent(work)}/context`));
  await inDiagnosticContext(origin, () => saveContext(work, checkpoint));
  return checkpoint;
}
/** 从本地二进制存储自动组装本次所需图片，稳定 ID 与 PNG 字节不随重试改变。 */
async function imagePayloads(ids: string[]) {
  return Promise.all([...new Set(ids)].map(async (id) => ({ id, data: encodeImage(await localImage(id)) })));
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
export async function retry(id: string, work?: string): Promise<Job> {
  const origin = diagnosticContext();
  const project = work ?? (await job(id)).project_id;
  const checkpoint = await inDiagnosticContext(origin, () => modelContext(project));
  const payload = {
    method: "POST",
    body: JSON.stringify({ ...checkpoint, ...(checkpoint.image_ids?.length ? { assets: await inDiagnosticContext(origin, () => imagePayloads(checkpoint.image_ids!)) } : {}) }),
  };
  return inDiagnosticContext(origin, () => modelRequest(`/jobs/${encodeURIComponent(id)}/retry`, payload));
}

/** 读取一页编号大于 after 的待送达公开事件（升序，可能少于全部）；后台同步据此补齐未在 SSE 中打开的会话。 */
export async function workEvents(
  work: string,
  after: number,
  signal?: AbortSignal,
): Promise<WorkEvent[]> {
  const origin = diagnosticContext();
  const service = apiBase();
  const result = await request<WorkEvent[]>(`/works/${encodeURIComponent(work)}/events?after=${after}`, {
    signal,
  });
  // 后台分页与 SSE 一样推进任务诊断；只观察本会话的新事件，返回原页供本地存储和 ACK 使用。
  if (Array.isArray(result)) for (const event of result
    .filter(event => event?.work_id === work && Number.isSafeInteger(event.id) && event.id > after)
    .sort((left, right) => left.id - right.id)) {
    if (event.type !== "job.updated" || !event.data) continue;
    const job = event.data;
    const stage = Array.isArray(job.progress) ? job.progress.at(-1)?.phase : undefined;
    inDiagnosticContext(origin, () => diagnosticTaskState("字效", job.id, job.status, { service, stage }));
  }
  return result;
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
