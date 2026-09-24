/** 模板存储边界：云端及本地保存请求使用 Protobuf，本地文件仍使用 JSON。 */
import { invoke, isTauri } from "@tauri-apps/api/core";
import { create, fromBinary, toBinary, toJson } from "@bufbuild/protobuf";
import { timestampDate } from "@bufbuild/protobuf/wkt";
import { apiBase } from "@/lib/api-base";
import {
  EffectTemplateEditorSchema,
  GetTemplateResponseSchema,
  ListTemplatesResponseSchema,
  SaveTemplateRequestSchema,
  SaveTemplateResponseSchema,
  type TemplateRecord,
} from "@/generated/imv/template/v1/template_pb";
import { defaultEditor, draftEffects, type Category, type Draft, type Editor, type EffectTrack, type Template } from "./model";

/** 当前模板库的存储位置，每次操作显式传递，避免切换后写入错误环境。 */
export type Environment = "local" | "cloud";

/** 本地操作仅在桌面中可用；IPC 字符串错误统一转成 Error 供现有弹窗显示。 */
async function local<T>(operation: string, id?: string, draft?: number[]): Promise<T> {
  if (!isTauri()) throw new Error("本地模式需要在桌面客户端中使用");
  try {
    return await invoke<T>("local_templates", { operation, id, draft });
  } catch (error) {
    throw error instanceof Error ? error : new Error(String(error));
  }
}

/** 还原现有模板视图的字段名称、可空时长及 UTC 时间。 */
function toTemplate(record: TemplateRecord): Template {
  if (!record.tracks || !record.createdAt || !record.updatedAt)
    throw new Error("模板响应缺少必要字段");
  return {
    name: record.name,
    description: record.description,
    effect_ids: record.effectIds,
    transition_duration_seconds: record.transitionDurationSeconds,
    template_id: record.templateId,
    created_at: timestampDate(record.createdAt).toISOString(),
    updated_at: timestampDate(record.updatedAt).toISOString(),
    effects: record.effects.map((effect) => ({
      id: effect.id,
      category: effect.category as Category,
      name: effect.name,
      effect_id: effect.effectId,
      parameters: effect.parameters,
      preview_url: effect.previewUrl,
    })),
    tracks: record.tracks.tracks.map((track): EffectTrack => {
      if (track.start === undefined || !track.editor)
        throw new Error("模板对象缺少必要字段");
      const editor = toJson(EffectTemplateEditorSchema, track.editor) as Partial<Editor>;
      if (Object.keys(defaultEditor).some((key) => !Object.prototype.hasOwnProperty.call(editor, key)))
        throw new Error("模板编辑配置缺少必要字段");
      return {
        id: track.id,
        target: track.target as EffectTrack["target"],
        start_mode: track.startMode as EffectTrack["start_mode"],
        start: track.start,
        duration: track.duration ?? null,
        editor: editor as Editor,
      };
    }),
  };
}

/** 按当前运行环境生成云端服务不可用时的本地模板提示。 */
function localHint(): string {
  return isTauri()
    ? "可返回主页选择或创建本地模板。"
    : "可使用桌面客户端切换到本地环境。";
}

/** 有界云端请求；读取可取消，写入失败由用户确认后重试。 */
async function cloud<T>(call: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 20_000);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    return await call(controller.signal);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timedOut)
      throw new Error(
        `请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。${localHint()}`,
      );
    if (error instanceof TypeError)
      throw new Error(`无法连接服务端，请确认 API 已启动后重试。${localHint()}`);
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

/** 在现有模板路由发送请求，并将 HTTP 错误转换为编辑器可显示的消息。 */
async function cloudRequest(path: string, method: "GET" | "POST" | "DELETE", signal: AbortSignal, body?: Uint8Array): Promise<Response> {
  const response = await fetch(`${apiBase()}${path}`, {
    method,
    headers: {
      Accept: "application/x-protobuf",
      ...(body ? { "Content-Type": "application/x-protobuf" } : {}),
    },
    body: body ? Uint8Array.from(body) : undefined,
    signal,
  });
  if (!response.ok) {
    const { detail } = await response.json().catch(() => ({})) as { detail?: unknown };
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((error: { loc?: unknown[]; msg?: string }) => `${error.loc?.slice(1).join(".")}：${error.msg}`).join("；")
      : `HTTP ${response.status}`;
    const hint = response.status >= 500
      ? ` ${localHint()}`
      : "";
    throw new Error(`${message}${hint}`);
  }
  return response;
}

/** 获取共享模板列表；组件卸载时取消请求。 */
export function listTemplates(signal?: AbortSignal, environment: Environment = "cloud"): Promise<Template[]> {
  return environment === "local" ? local("list") : cloud(
    async (requestSignal) => {
      const response = await cloudRequest("/template", "GET", requestSignal);
      return fromBinary(ListTemplatesResponseSchema, new Uint8Array(await response.arrayBuffer()))
        .templates.map(toTemplate);
    },
    signal,
  );
}

/** 切换时读取最新详情，避免列表缓存覆盖其他客户端的更新。 */
export function getTemplate(id: string, environment: Environment = "cloud", signal?: AbortSignal): Promise<Template> {
  return environment === "local" ? local("get", id) : cloud(async (requestSignal) => {
    const response = await cloudRequest(`/template/${encodeURIComponent(id)}`, "GET", requestSignal);
    const message = fromBinary(GetTemplateResponseSchema, new Uint8Array(await response.arrayBuffer()));
    if (!message.template) throw new Error("模板响应缺少内容");
    return toTemplate(message.template);
  }, signal);
}

/** 统一创建、更新及另存为；只有调用方明确传 ID 时才覆盖已有模板。 */
export function saveTemplate(draft: Draft, id?: string, environment: Environment = "cloud"): Promise<Template> {
  if (!draft.name.trim()) return Promise.reject(new Error("请输入模板名称"));
  const effect_ids = draftEffects(draft);
  if (!effect_ids.length)
    return Promise.reject(new Error("请至少选择一个效果"));
  const message = create(SaveTemplateRequestSchema, {
    name: draft.name.trim(),
    description: draft.description.trim(),
    effectIds: effect_ids,
    transitionDurationSeconds: draft.transition_duration_seconds,
    tracks: {
      tracks: draft.tracks.map((track) => ({
        id: track.id,
        target: track.target,
        startMode: track.start_mode,
        start: track.start,
        duration: track.duration ?? undefined,
        editor: track.editor,
      })),
    },
    templateId: id,
  });
  const payload = toBinary(SaveTemplateRequestSchema, message);
  if (environment === "local") return local("save", id, Array.from(payload));
  return cloud(async (requestSignal) => {
    const response = await cloudRequest("/template", "POST", requestSignal, payload);
    const saved = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await response.arrayBuffer()));
    if (!saved.template) throw new Error("模板响应缺少内容");
    return toTemplate(saved.template);
  });
}

/** 删除单个模板；成功后由调用方更新列表。 */
export function deleteTemplate(id: string, environment: Environment = "cloud"): Promise<void> {
  return environment === "local" ? local("delete", id) : cloud(async (requestSignal) => {
    await cloudRequest(`/template/${encodeURIComponent(id)}`, "DELETE", requestSignal);
  });
}
