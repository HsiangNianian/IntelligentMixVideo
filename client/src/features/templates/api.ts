/** 模板存储边界：云端沿用 HTTP，本地通过 Tauri 写入客户端 data/template。 */
import { invoke, isTauri } from "@tauri-apps/api/core";
import { apiBase } from "@/lib/api-base";
import { selectedEffects, type Draft, type Template } from "./model";

/** 当前模板库的存储位置，每次操作显式传递，避免切换后写入错误环境。 */
export type Environment = "local" | "cloud";

/** 本地操作仅在桌面中可用；IPC 字符串错误统一转成 Error 供现有弹窗显示。 */
async function local<T>(operation: string, id?: string, draft?: Draft): Promise<T> {
  if (!isTauri()) throw new Error("本地模式需要在桌面客户端中使用");
  try {
    return await invoke<T>("local_templates", { operation, id, draft });
  } catch (error) {
    throw error instanceof Error ? error : new Error(String(error));
  }
}

/** 有界请求，卸载可中断读取；写入失败不自动重试，防止重复创建。 */
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  // 仅服务不可用时建议本地存储；浏览器需先使用桌面客户端。
  const localHint = isTauri()
    ? "可在「当前环境」中切换到本地环境。"
    : "可使用桌面客户端切换到本地环境。";
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 20_000);
  try {
    const response = await fetch(`${apiBase()}/template${path}`, {
      ...options,
      signal: controller.signal,
      headers: options.body
        ? { "Content-Type": "application/json" }
        : undefined,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = body.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map(
                  (item: { loc?: string[]; msg?: string }) =>
                    `${item.loc?.slice(1).join(".") || "配置"}：${item.msg}`,
                )
                .join("；")
            : `请求失败（${response.status}）`;
      throw new Error(response.status >= 500 ? `${message} ${localHint}` : message);
    }
    return response.status === 204
      ? (undefined as T)
      : ((await response.json()) as T);
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (controller.signal.aborted)
      throw new Error(
        `请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。${localHint}`,
      );
    if (error instanceof TypeError)
      throw new Error(`无法连接服务端，请确认 API 已启动后重试。${localHint}`);
    throw error;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
}

/** 获取共享模板列表；组件卸载时取消请求。 */
export function listTemplates(signal?: AbortSignal, environment: Environment = "cloud"): Promise<Template[]> {
  return environment === "local" ? local("list") : request("", { signal });
}

/** 切换时读取最新详情，避免列表缓存覆盖其他客户端的更新。 */
export function getTemplate(id: string, environment: Environment = "cloud"): Promise<Template> {
  return environment === "local" ? local("get", id) : request(`/${encodeURIComponent(id)}`);
}

/** 统一创建、更新及另存为；只有调用方明确传 ID 时才覆盖已有模板。 */
export function saveTemplate(draft: Draft, id?: string, environment: Environment = "cloud"): Promise<Template> {
  if (!draft.name.trim()) return Promise.reject(new Error("请输入模板名称"));
  const effect_ids = selectedEffects(draft.editor);
  if (!effect_ids.length)
    return Promise.reject(new Error("请至少选择一个效果"));
  if (environment === "local") return local("save", id, draft);
  return request("", {
    method: "POST",
    body: JSON.stringify({
      ...draft,
      name: draft.name.trim(),
      description: draft.description.trim(),
      effect_ids,
      ...(id ? { template_id: id } : {}),
    }),
  });
}

/** 删除单个模板；成功后由调用方更新列表。 */
export function deleteTemplate(id: string, environment: Environment = "cloud"): Promise<void> {
  return environment === "local" ? local("delete", id) : request(`/${encodeURIComponent(id)}`, { method: "DELETE" });
}
