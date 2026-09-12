/** 模板 HTTP 边界：统一超时、错误消息和四个接口，不向展示组件泄露 fetch 细节。 */
import { selectedEffects, type Draft, type Template } from "./model";

/** client/.env 可覆盖 API 地址；未配置或留空时连接本机默认端口。 */
const base = (
  import.meta.env.VITE_API_URL?.trim() || "http://localhost:8000"
).replace(/\/+$/, "");

/** 有界请求，卸载可中断读取；写入失败不自动重试，防止重复创建。 */
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 20_000);
  try {
    const response = await fetch(`${base}/template${path}`, {
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
      throw new Error(message);
    }
    return response.status === 204
      ? (undefined as T)
      : ((await response.json()) as T);
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (controller.signal.aborted)
      throw new Error(
        "请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。",
      );
    if (error instanceof TypeError)
      throw new Error("无法连接服务端，请确认 API 已启动后重试。");
    throw error;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
}

/** 获取共享模板列表；组件卸载时取消请求。 */
export function listTemplates(signal?: AbortSignal): Promise<Template[]> {
  return request("", { signal });
}

/** 切换时读取最新详情，避免列表缓存覆盖其他客户端的更新。 */
export function getTemplate(id: string): Promise<Template> {
  return request(`/${encodeURIComponent(id)}`);
}

/** 统一创建、更新及另存为；只有调用方明确传 ID 时才覆盖已有模板。 */
export function saveTemplate(draft: Draft, id?: string): Promise<Template> {
  if (!draft.name.trim()) return Promise.reject(new Error("请输入模板名称"));
  const effect_ids = selectedEffects(draft.editor);
  if (!effect_ids.length)
    return Promise.reject(new Error("请至少选择一个效果"));
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
export function deleteTemplate(id: string): Promise<void> {
  return request(`/${encodeURIComponent(id)}`, { method: "DELETE" });
}
