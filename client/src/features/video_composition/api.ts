/** 合成提交与查询使用当前客户端 IMS 凭据；后台 ASR、切片和素材匹配仍由服务端配置。 */
import { readSettings } from "@/features/settings/api";
import { apiBase } from "@/lib/api-base";

/** 凭据只放请求头，不进入合成输入记录；每次操作读取最新保存值，失败不重试写入。 */
async function request(path: string, body?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const config = (await readSettings()).ims;
  const response = await fetch(`${apiBase()}/api/v1/video-compositions${path}`, {
    method: body ? "POST" : "GET",
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(config ? { "X-IMS-Config": encodeURIComponent(JSON.stringify(config)) } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw new Error(`视频合成请求失败（${response.status}）`);
  return response.json();
}

/** 提交现有合成协议，返回创建接口 data 中的任务标识。 */
export async function createComposition(payload: Record<string, unknown>): Promise<string> {
  const { data } = await request("", payload);
  return data as string;
}

/** 新成片查询返回 ZOS 地址；仍附带 IMS 凭据供历史任务刷新临时地址。 */
export function getComposition(taskId: string) {
  return request(`/${encodeURIComponent(taskId)}`);
}
