/** 设置插件描述从后端读取；值经 Tauri 保存，浏览器仅保留当前页面进程内的配置。 */
import { invoke, isTauri } from "@tauri-apps/api/core";

/** 第一版只支持标量配置；描述由模块的 Pydantic 模型生成。 */
export type Values = Record<string, string | number | boolean>;
export type Plugin = {
  id: string;
  name: string;
  schema: {
    properties: Record<string, {
      type: "string" | "number" | "integer" | "boolean";
      title?: string;
      format?: string;
      default?: string | number | boolean;
      minimum?: number;
      maximum?: number;
      exclusiveMinimum?: number;
    }>;
    required?: string[];
  };
};

// ponytail: 浏览器只用于预览，刷新即丢失；持久化由桌面命令负责。
let browserSettings: Record<string, Values> = {};

// 与现有业务接口共用 VITE_API_URL；设置和切片路由位于服务根路径。
const base = (import.meta.env.VITE_API_URL?.trim() || "http://localhost:20070").replace(/\/+$/, "");

/** 每次打开设置读取目录，调用方卸载时取消请求。 */
export async function listPlugins(signal?: AbortSignal): Promise<Plugin[]> {
  const response = await fetch(`${base}/api/settings/plugins`, { signal });
  if (!response.ok) throw new Error("读取设置插件失败");
  return response.json();
}

/** 桌面从固定文件读取；返回副本，表单编辑不提前改变已保存值。 */
export async function readSettings(): Promise<Record<string, Values>> {
  return isTauri() ? invoke("local_settings") : structuredClone(browserSettings);
}

/** 保存单个插件，不发送配置到后端；失败交由表单展示。 */
export async function saveSettings(id: string, values: Values): Promise<void> {
  if (isTauri()) await invoke("local_settings", { id, values });
  else browserSettings = { ...browserSettings, [id]: structuredClone(values) };
}

/** 独立切片联调入口：读取已保存配置并随本次请求发送，不接入后台视频合成。 */
export async function requestSegmentation(payload: { script: string; asr_result: Record<string, unknown> }) {
  const config = (await readSettings()).segmentation;
  const response = await fetch(`${base}/segmentations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, ...(config ? { config } : {}) }),
  });
  if (!response.ok) throw new Error(`切片请求失败（${response.status}）`);
  return response.json();
}
