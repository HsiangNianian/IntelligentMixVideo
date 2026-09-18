/** 切片请求读取本模块最新保存的客户端配置，仅随本次请求发送。 */
import { readSettings } from "@/features/settings/api";
import { apiBase } from "@/lib/api-base";

/** 独立切片联调入口：读取已保存配置并随本次请求发送，不接入后台视频合成。 */
export async function requestSegmentation(payload: { script: string; asr_result: Record<string, unknown> }) {
  const config = (await readSettings()).segmentation;
  const response = await fetch(`${apiBase()}/segmentations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, ...(config ? { config } : {}) }),
  });
  if (!response.ok) throw new Error(`切片请求失败（${response.status}）`);
  return response.json();
}
