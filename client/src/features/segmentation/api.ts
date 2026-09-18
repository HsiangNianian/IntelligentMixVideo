/** Debug 切片请求携带本模块已保存配置；普通模式使用服务端环境配置。 */
import { readSettings } from "@/features/settings/api";
import { apiBase } from "@/lib/api-base";

/** 独立切片联调入口：读取已保存配置并随本次请求发送，不接入后台视频合成。 */
export async function requestSegmentation(payload: { script: string; asr_result: Record<string, unknown> }) {
  const saved = import.meta.env.IMV_DEBUG === "true" ? (await readSettings()).segmentation : undefined;
  // HTTP 策略只在 Debug 内置后端启动时加载，不加入现有请求配置契约。
  const config = saved && Object.fromEntries(Object.entries(saved).filter(([key]) => key !== "allow_insecure_llm_http"));
  const response = await fetch(`${apiBase()}/segmentations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, ...(config ? { config } : {}) }),
  });
  if (!response.ok) throw new Error(`切片请求失败（${response.status}）`);
  return response.json();
}
