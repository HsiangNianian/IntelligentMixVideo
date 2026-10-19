/** 模板存储边界：云端及本地保存请求使用 Protobuf，本地文件仍使用 JSON。 */
import { updateInvoke as invoke } from "@/lib/update-guard";
import { updateFetch as fetch } from "@/lib/update-guard";
import { isTauri } from "@tauri-apps/api/core";
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
import { defaultEditor, draftEffects, isKeywordTarget, type Category, type Draft, type Editor, type EffectTrack, type RemotionClip, type Template, type TemplateSummary } from "./model";
import { validateTemplateTracks } from "./tracks";
import { BOUND_TEXT_PARAMETERS, validateClipSlots } from "./clips";

/** 当前模板库的存储位置，每次操作显式传递，避免切换后写入错误环境。 */
export type Environment = "local" | "cloud";

/** 本地操作仅在桌面中可用；IPC 字符串错误统一转成 Error 供现有弹窗显示。 */
async function local<T>(operation: string, id?: string, draft?: number[], library?: string): Promise<T> {
  if (!isTauri()) throw new Error("本地模式需要在桌面客户端中使用");
  try {
    return await invoke<T>("local_templates", { operation, id, draft, library });
  } catch (error) {
    throw error instanceof Error ? error : new Error(String(error));
  }
}

/** 还原现有模板视图的字段名称、可空时长及 UTC 时间；`remotion` 轨道拆成独立的片段列表，不进入 IMS 对象。 */
function toTemplate(record: TemplateRecord): Template {
  if (!record.tracks || !record.createdAt || !record.updatedAt)
    throw new Error("模板响应缺少必要字段");
  const wireTracks = record.tracks.tracks;
  if (record.transitionMode !== undefined && !["sequence", "random"].includes(record.transitionMode))
    throw new Error("转场设置方式无效");
  return {
    name: record.name,
    description: record.description,
    effect_ids: record.effectIds,
    transition_duration_seconds: record.transitionDurationSeconds,
    transition_mode: record.transitionMode as Draft["transition_mode"],
    transition_enabled: record.transitionEnabled ?? true,
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
    tracks: wireTracks.filter((track) => track.target !== "remotion").map((track): EffectTrack => {
      if ((track.target !== "transition" && track.start === undefined) || !track.editor)
        throw new Error("模板对象缺少必要字段");
      const received = toJson(EffectTemplateEditorSchema, track.editor) as Partial<Editor>;
      if (Object.keys(defaultEditor).some((key) => !key.startsWith("subtitleKeyword") && !key.startsWith("titleKeyword") && key !== "titleFont" && key !== "subtitleFont" && !Object.prototype.hasOwnProperty.call(received, key)))
        throw new Error("模板编辑配置缺少必要字段");
      const editor = { ...defaultEditor, ...received };
      return {
        id: track.id,
        target: track.target as EffectTrack["target"],
        ...(track.language !== undefined ? { language: track.language as EffectTrack["language"] } : {}),
        ...(track.enabled !== undefined ? { enabled: track.enabled } : {}),
        ...(track.startMode ? { start_mode: track.startMode as EffectTrack["start_mode"] } : {}),
        ...(track.start !== undefined ? { start: track.start } : {}),
        duration: track.duration ?? null,
        ...(track.transitionBoundary !== undefined ? { transition_boundary: track.transitionBoundary } : {}),
        editor,
      };
    }),
    clips: wireTracks.filter((track) => track.target === "remotion").map((track): RemotionClip => {
      if (track.start === undefined || !track.spriteId)
        throw new Error("模板对象缺少必要字段");
      const remotion = track.editor?.remotion;
      const parameters: RemotionClip["parameters"] = {};
      for (const item of remotion?.parameters ?? []) {
        const value = item.stringListValue ? [...item.stringListValue.values] : item.stringValue ?? item.numberValue ?? item.boolValue;
        if (value !== undefined) parameters[item.key] = value;
      }
      const transform: RemotionClip["transform"] = {};
      for (const name of ["x", "y", "scale", "rotation", "opacity"] as const) {
        const value = remotion?.transform?.[name];
        if (value !== undefined) transform[name] = value;
      }
      const purpose = remotion?.businessTarget;
      if (purpose && !["title", "subtitle", "decoration"].includes(purpose)) throw new Error("Remotion 片段用途无效");
      // 旧用途保持独立叠加，不能在读取时隐式迁移为替代 IMS 文字的槽位。
      const textTarget = track.textTarget === "title" || track.textTarget === "subtitle" || (track.textTarget && isKeywordTarget(track.textTarget)) ? track.textTarget : undefined;
      // 两种文字绑定都由总线填入正文，清除旧覆盖但保留样式。
      if (purpose && (textTarget || purpose === "title" || purpose === "subtitle"))
        for (const key of BOUND_TEXT_PARAMETERS) delete parameters[key];
      return {
        id: track.id, sprite_id: track.spriteId, start: track.start, duration: track.duration ?? null, parameters, transform,
        ...(purpose ? { business_target: purpose as RemotionClip["business_target"] } : {}),
        ...(textTarget ? { text_target: textTarget } : {}),
        ...(track.language !== undefined ? { language: track.language as RemotionClip["language"] } : {}),
        ...(track.transitionBoundary !== undefined ? { transition_boundary: track.transitionBoundary } : {}),
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
export function listTemplates(signal?: AbortSignal, environment: Environment = "cloud"): Promise<TemplateSummary[]> {
  return environment === "local" ? local("list") : cloud(
    async (requestSignal) => {
      const response = await cloudRequest("/template", "GET", requestSignal);
      return fromBinary(ListTemplatesResponseSchema, new Uint8Array(await response.arrayBuffer()))
        .templates.map((record) => {
          if (!record.createdAt || !record.updatedAt) throw new Error("模板摘要缺少时间");
          return { template_id: record.templateId, name: record.name, description: record.description,
            created_at: timestampDate(record.createdAt).toISOString(), updated_at: timestampDate(record.updatedAt).toISOString(),
            title: record.title, subtitle: record.subtitle, track_count: record.trackCount, validation_error: record.validationError };
        });
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

/** 一次摆放的 editor：按需保存参数覆盖、宿主变换与旧用途；新槽位不重复写入旧用途。 */
function clipEditor(clip: RemotionClip) {
  const parameters = Object.entries(clip.parameters).map(([key, value]) => ({
    key,
    ...(Array.isArray(value) ? { stringListValue: { values: value } }
      : typeof value === "string" ? { stringValue: value }
        : typeof value === "number" ? { numberValue: value }
          : { boolValue: value }),
  }));
  const transform = Object.keys(clip.transform).length ? clip.transform : undefined;
  const businessTarget = clip.business_target ? clip.text_target ?? clip.business_target : undefined;
  return parameters.length || transform || businessTarget ? { remotion: { parameters, transform, businessTarget } } : {};
}

/**
 * 统一创建、更新及另存为；只有调用方明确传 ID 时才覆盖已有模板，本地更新携带打开时所属的库。
 * 保存要求标题和底部字幕各一个，基础文字允许不选择效果。
 * 片段在线路上是 `remotion` 轨道，排在 IMS 对象之后，与模板在同一次请求里原子保存；本地模板不支持，带片段的草稿直接拒绝。
 */
export async function saveTemplate(draft: Draft, id?: string, environment: Environment = "cloud", library?: string): Promise<Template> {
  if (!draft.name.trim()) return Promise.reject(new Error("请输入模板名称"));
  if (environment === "local" && draft.clips.length)
    return Promise.reject(new Error("本地模板不支持 Remotion 资产"));
  validateTemplateTracks(draft.tracks);
  validateClipSlots(draft);
  for (const target of ["title", "subtitle"])
    if (draft.tracks.filter((track) => track.target === target && track.language !== "en").length !== 1)
      throw new Error("保存模板需要标题和底部字幕各一个");
  const effect_ids = draftEffects(draft);
  const message = create(SaveTemplateRequestSchema, {
    name: draft.name.trim(),
    description: draft.description.trim(),
    effectIds: effect_ids,
    transitionDurationSeconds: draft.transition_duration_seconds,
    transitionMode: draft.transition_mode ?? "sequence",
    transitionEnabled: draft.transition_enabled ?? true,
    tracks: {
      tracks: [
        ...draft.tracks.map((track) => ({
          id: track.id,
          target: track.target,
          language: track.language,
          enabled: track.enabled,
          startMode: track.start_mode,
          start: track.start,
          duration: track.duration ?? undefined,
          transitionBoundary: track.transition_boundary,
          editor: track.editor,
        })),
        // 片段的 editor 只带这次摆放的参数覆盖与宿主变换；没有修改时必须是空消息而不是缺省。
        ...draft.clips.map((clip) => ({
          id: clip.id,
          target: "remotion",
          startMode: "seconds",
          start: clip.start,
          duration: clip.duration ?? undefined,
          editor: clipEditor(clip),
          spriteId: clip.sprite_id,
          textTarget: clip.text_target,
          language: clip.language,
          transitionBoundary: clip.transition_boundary,
        })),
      ],
    },
    templateId: id,
  });
  const payload = toBinary(SaveTemplateRequestSchema, message);
  if (environment === "local") return local("save", id, Array.from(payload), library);
  return cloud(async (requestSignal) => {
    const response = await cloudRequest("/template", "POST", requestSignal, payload);
    const saved = fromBinary(SaveTemplateResponseSchema, new Uint8Array(await response.arrayBuffer()));
    if (!saved.template) throw new Error("模板响应缺少内容");
    return toTemplate(saved.template);
  });
}

/** 删除单个模板；成功后由调用方更新列表。 */
export function deleteTemplate(id: string, environment: Environment = "cloud", library?: string): Promise<void> {
  return environment === "local" ? local("delete", id, undefined, library) : cloud(async (requestSignal) => {
    await cloudRequest(`/template/${encodeURIComponent(id)}`, "DELETE", requestSignal);
  });
}
