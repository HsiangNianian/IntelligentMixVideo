/** 设置插件描述从后端读取；值经 Tauri 保存，浏览器仅保留当前页面进程内的配置。 */
import { invoke, isTauri } from "@tauri-apps/api/core";
import { apiBase } from "@/lib/api-base";

/** 第一版只支持标量配置；描述由模块的 Pydantic 模型生成。 */
export type Values = Record<string, string | number | boolean>;
export type Plugin = {
  id: string;
  name: string;
  description?: string;
  schema: {
    properties: Record<string, {
      type: "string" | "number" | "integer" | "boolean";
      title?: string;
      format?: string;
      default?: string | number | boolean;
      minimum?: number;
      maximum?: number;
      exclusiveMinimum?: number;
      exclusiveMaximum?: number;
      pattern?: string;
      minLength?: number;
      maxLength?: number;
    }>;
    required?: string[];
  };
};

// ponytail: 浏览器只用于预览，刷新即丢失；持久化由桌面命令负责。
let browserSettings: Record<string, Values> = {};

/** 每次打开设置读取目录，调用方卸载时取消请求。 */
export async function listPlugins(signal?: AbortSignal): Promise<Plugin[]> {
  const response = await fetch(`${apiBase()}/api/settings/plugins${import.meta.env.IMV_DEBUG === "true" ? "" : "?client_only=true"}`, { signal });
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
