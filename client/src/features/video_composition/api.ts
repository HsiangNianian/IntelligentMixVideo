/** 合成提交与查询使用当前客户端 IMS 凭据；后台 ASR、切片和素材匹配仍由服务端配置。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { readSettings } from "@/features/settings/api";
import { apiBase } from "@/lib/api-base";

export type CompositionMode = "standard" | "materials_voice" | "materials_silent";
export type CompositionTask = {
  taskId: string;
  status: "queued" | "processing" | "succeeded" | "failed";
  stage: string;
  result: { videoUrl: string; durationSeconds: number } | null;
  error: { code: string; message: string; stage: string } | null;
  createdAt: string;
  updatedAt: string;
};

/** 凭据只放请求头，不进入合成输入记录；每次操作读取最新保存值，失败不重试写入。 */
async function request<T>(path: string, body?: Record<string, unknown>, signal?: AbortSignal, base = apiBase()): Promise<T> {
  const config = (await readSettings()).ims;
  const response = await fetch(`${base}/api/v1/video-compositions${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(config ? { "X-IMS-Config": encodeURIComponent(JSON.stringify(config)) } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detail = typeof error?.detail === "string" ? error.detail : typeof error?.message === "string" ? error.message : "视频合成请求失败";
    throw new Error(`${detail}（${response.status}）`);
  }
  return response.json();
}

/** 提交现有合成协议，返回创建接口 data 中的任务标识。 */
export async function createComposition(payload: Record<string, unknown>): Promise<string> {
  const { data } = await request<{ data: string }>("", payload);
  return data;
}

/** 新成片查询返回 ZOS 地址；仍附带 IMS 凭据供历史任务刷新临时地址。 */
export function getComposition(taskId: string, signal?: AbortSignal, base?: string) {
  return request<CompositionTask>(`/${encodeURIComponent(taskId)}`, undefined, signal, base);
}

/** 操练场只传模式和模板，固定内容由服务端读取；提交不自动重试。 */
export async function createPlayground(mode: CompositionMode, styleId: string, base?: string): Promise<string> {
  const result = await request<{ data: string }>("/playground", { mode, styleId }, undefined, base);
  return result.data;
}
