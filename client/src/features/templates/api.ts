/** 模板存储边界：保存请求使用 Protobuf，读取 JSON 响应并兼容旧服务端的二进制响应。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { create, fromBinary, fromJson, toBinary, toJson, type DescMessage } from "@bufbuild/protobuf";
import { timestampDate } from "@bufbuild/protobuf/wkt";
import { apiBase } from "@/lib/api-base";
import {
  EffectTemplateEditorSchema,
  GetTemplateResponseSchema,
  ListTemplatesResponseSchema,
  SaveTemplateRequestSchema,
  SaveTemplateResponseSchema,
  SplitScreenSchema,
  AvatarEffectsSchema,
  FrameMaskSchema,
  type TemplateRecord,
} from "@/generated/imv/template/v1/template_pb";
import { defaultEditor, draftEffects, type Category, type CoverInfo, type Draft, type Editor, type EffectTrack, type RemotionClip, type Template, type TemplateSummary } from "./model";
import { validateTemplateTracks } from "./tracks";
import { BOUND_TEXT_PARAMETERS, validateClipSlots } from "./clips";
import { validateFrameMask, type FrameMask } from "./frameMask";
import { defaultSplitScreen, validateSplitScreen, type SplitScreen } from "./splitScreen";
import { defaultAvatarEffects, validateAvatarEffects, type AvatarEffects } from "./avatarEffects";
import { defaultAvatarPreview, validateAvatarPreview } from "./avatarPreview";

/** 还原现有模板视图的字段名称、可空时长及 UTC 时间；`remotion` 轨道拆成独立的片段列表，不进入 IMS 对象。 */
function toTemplate(record: TemplateRecord): Template {
  if (!record.tracks || !record.createdAt || !record.updatedAt)
    throw new Error("模板响应缺少必要字段");
  const wireTracks = record.tracks.tracks;
  if (record.transitionMode !== undefined && !["sequence", "random"].includes(record.transitionMode))
    throw new Error("转场设置方式无效");
  return {
    ...(record.cover ? { cover: record.cover } : {}),
    name: record.name,
    description: record.description,
    effect_ids: record.effectIds,
    transition_duration_seconds: record.transitionDurationSeconds,
    transition_mode: record.transitionMode as Draft["transition_mode"],
    transition_enabled: record.transitionEnabled ?? true,
    ...(record.avatarPreview ? { avatar_preview: validateAvatarPreview({ x: record.avatarPreview.x ?? defaultAvatarPreview.x, y: record.avatarPreview.y ?? defaultAvatarPreview.y, source: record.avatarPreview.source ?? defaultAvatarPreview.source }) } : {}),
    ...(record.frameMask ? { frame_mask: validateFrameMask(toJson(FrameMaskSchema, record.frameMask) as unknown as FrameMask) } : {}),
    ...(record.avatarEffects ? { avatar_effects: (() => {
      const value = { ...defaultAvatarEffects(), ...toJson(AvatarEffectsSchema, record.avatarEffects) as Partial<AvatarEffects> };
      validateAvatarEffects(value);
      return value;
    })() } : {}),
    ...(record.splitScreen ? { split_screen: (() => {
      const raw = toJson(SplitScreenSchema, record.splitScreen) as Partial<SplitScreen>;
      const defaults = defaultSplitScreen();
      const layout = { ...defaults, ...raw, material: { ...defaults.material, ...raw.material }, avatar: { ...defaults.avatar, ...raw.avatar },
        subtitle: { ...defaults.subtitle, ...raw.subtitle }, subtitleEn: { ...defaults.subtitleEn, ...raw.subtitleEn } };
      validateSplitScreen(layout);
      return layout;
    })() } : {}),
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
      const textTarget = track.textTarget;
      // 两种文字绑定都由总线填入正文，清除旧覆盖但保留样式。
      if (purpose && (textTarget === "title" || textTarget === "subtitle" || purpose === "title" || purpose === "subtitle"))
        for (const key of BOUND_TEXT_PARAMETERS) delete parameters[key];
      return {
        id: track.id, sprite_id: track.spriteId, start: track.start, duration: track.duration ?? null, parameters, transform,
        ...(purpose ? { business_target: purpose as RemotionClip["business_target"] } : {}),
        ...(textTarget === "title" || textTarget === "subtitle" ? { text_target: textTarget } : {}),
        ...(track.language !== undefined ? { language: track.language as RemotionClip["language"] } : {}),
        ...(track.transitionBoundary !== undefined ? { transition_boundary: track.transitionBoundary } : {}),
      };
    }),
  };
}

/** 有界云端请求；读取可取消，写入失败由用户确认后重试。 */
async function cloud<T>(call: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal, timeoutMessage = "请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。"): Promise<T> {
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
      throw new Error(timeoutMessage);
    if (error instanceof TypeError)
      throw new Error("无法连接服务端，请确认 API 已启动后重试。");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

/** ZOS 确认已发布的 PNG 缺失时，主页可按原素材重新发布。 */
class MissingCoverObject extends Error {}

/** 在现有模板路由发送请求，并将 HTTP 错误转换为编辑器可显示的消息。 */
async function cloudRequest(path: string, method: "GET" | "POST" | "DELETE" | "PUT", signal: AbortSignal, body?: Uint8Array | FormData): Promise<Response> {
  const response = await fetch(`${apiBase()}${path}`, {
    method,
    headers: {
      Accept: "application/json",
      ...(body instanceof Uint8Array ? { "Content-Type": "application/x-protobuf" } : {}),
    },
    body: body instanceof Uint8Array ? Uint8Array.from(body) : body,
    signal,
  });
  if (!response.ok) {
    const { detail } = await response.json().catch(() => ({})) as { detail?: unknown };
    if (response.status === 404 && detail && typeof detail === "object" && "code" in detail && detail.code === "cover_object_missing")
      throw new MissingCoverObject("封面图片已丢失");
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((error: { loc?: unknown[]; msg?: string }) => `${error.loc?.slice(1).join(".")}：${error.msg}`).join("；")
      : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return response;
}

/** 按响应类型兼容旧服务端；新增 JSON 字段不阻断旧客户端读取已知字段。 */
async function readTemplateResponse<T extends DescMessage>(schema: T, response: Response) {
  const mediaType = response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
  return mediaType === "application/x-protobuf"
    ? fromBinary(schema, new Uint8Array(await response.arrayBuffer()))
    : fromJson(schema, await response.json(), { ignoreUnknownFields: true });
}

/** 获取共享模板列表；组件卸载时取消请求。 */
export function listTemplates(signal?: AbortSignal): Promise<TemplateSummary[]> {
  return cloud(
    async (requestSignal) => {
      const response = await cloudRequest("/template", "GET", requestSignal);
      return (await readTemplateResponse(ListTemplatesResponseSchema, response))
        .templates.map((record) => {
          if (!record.createdAt || !record.updatedAt) throw new Error("模板摘要缺少时间");
          return { ...(record.cover ? { cover: record.cover } : {}), template_id: record.templateId, name: record.name, description: record.description,
            created_at: timestampDate(record.createdAt).toISOString(), updated_at: timestampDate(record.updatedAt).toISOString(),
            title: record.title, subtitle: record.subtitle, track_count: record.trackCount, validation_error: record.validationError };
        });
    },
    signal,
  );
}

/** 切换时读取最新详情，避免列表缓存覆盖其他客户端的更新。 */
export function getTemplate(id: string, signal?: AbortSignal): Promise<Template> {
  return cloud(async (requestSignal) => {
    const response = await cloudRequest(`/template/${encodeURIComponent(id)}`, "GET", requestSignal);
    const message = await readTemplateResponse(GetTemplateResponseSchema, response);
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
 * 统一创建、更新及另存为；只有调用方明确传 ID 时才覆盖已有模板。
 * 保存要求标题和底部字幕各一个，基础文字允许不选择效果。
 * 片段在线路上是 `remotion` 轨道，排在 IMS 对象之后，与模板在同一次请求里原子保存。
 */
export async function saveTemplate(draft: Draft, id?: string, baselineHadMask = false, coverSource?: string): Promise<Template> {
  if (!draft.name.trim()) return Promise.reject(new Error("请输入模板名称"));
  validateTemplateTracks(draft.tracks);
  validateClipSlots(draft);
  if (draft.frame_mask) validateFrameMask(draft.frame_mask);
  if (draft.split_screen) validateSplitScreen(draft.split_screen);
  if (draft.avatar_effects) validateAvatarEffects(draft.avatar_effects);
  if (draft.avatar_preview) validateAvatarPreview(draft.avatar_preview);
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
    // 只有保存基线带蒙版、当前草稿已移除时才发送空消息；普通无蒙版保存仍兼容旧服务端。
    frameMask: draft.frame_mask ? {
      top: { ...draft.frame_mask.top, imageId: draft.frame_mask.top.imageId ?? "" },
      bottom: { ...draft.frame_mask.bottom, imageId: draft.frame_mask.bottom.imageId ?? "" },
    } : (baselineHadMask ? {} : undefined),
    splitScreen: draft.split_screen,
    avatarEffects: draft.avatar_effects,
    avatarPreview: draft.avatar_preview,
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
    coverSource,
  });
  const payload = toBinary(SaveTemplateRequestSchema, message);
  return cloud(async (requestSignal) => {
    const response = await cloudRequest("/template", "POST", requestSignal, payload);
    const saved = await readTemplateResponse(SaveTemplateResponseSchema, response);
    if (!saved.template) throw new Error("模板响应缺少内容");
    return toTemplate(saved.template);
  });
}

/** 删除单个模板；成功后由调用方更新列表。 */
export function deleteTemplate(id: string): Promise<void> {
  return cloud(async (requestSignal) => {
    await cloudRequest(`/template/${encodeURIComponent(id)}`, "DELETE", requestSignal);
    coverBlobs.delete(apiBase() + "/" + id);
    publishedCovers.delete(apiBase() + "/" + id);
  });
}

/** 静态图片由服务端统一校验；成功才返回可长期保存的内容引用。 */
export function uploadFrameMaskImage(file: File, signal: AbortSignal): Promise<string> {
  return cloud(async (requestSignal) => {
    const body = new FormData();
    body.append("file", file);
    const response = await cloudRequest("/template/frame-mask-images", "POST", requestSignal, body);
    const result = await response.json() as { imageId?: unknown };
    if (typeof result.imageId !== "string" || !/^[a-f0-9]{64}$/.test(result.imageId)) throw new Error("图片上传响应无效");
    return result.imageId;
  }, signal, "图片上传超时，原效果已保留，请重试。");
}

/** 缩略图与预览共用固定服务端图片路由，不持久化临时 blob 地址。 */
export function frameMaskImageUrl(imageId: string): string {
  return `${apiBase()}/template/frame-mask-images/${encodeURIComponent(imageId)}`;
}

/** 有界、可取消地读取图片；blob 由预览组件负责解码及释放。 */
export function readFrameMaskImage(imageId: string, signal: AbortSignal): Promise<Blob> {
  return cloud(async (requestSignal) => (await cloudRequest(`/template/frame-mask-images/${encodeURIComponent(imageId)}`, "GET", requestSignal)).blob(), signal, "蒙版图片读取超时，请刷新预览重试。");
}

/** 画廊、列表和操练场共享进行中的读取及已发布的 PNG；内存最多保留最近两页（每页 15 项）。 */
const publishedCovers = new Map<string, { revision: string; image: Promise<Blob> }>();

/** 按已发布版本复用，在固定 URL 读取新版。单张卡片卸载不会中止其他视图仍在等待的请求。 */
export function readTemplateCover(id: string, revision: string): Promise<Blob> {
  const key = apiBase() + "/" + id;
  const cached = publishedCovers.get(key);
  if (cached?.revision === revision) {
    publishedCovers.delete(key);
    publishedCovers.set(key, cached);
    return cached.image;
  }
  const image = cloud(async (signal) => (await cloudRequest(`/template/${encodeURIComponent(id)}/cover`, "GET", signal)).blob())
    .catch((error) => {
      if (publishedCovers.get(key)?.image === image) publishedCovers.delete(key);
      throw error;
    });
  publishedCovers.set(key, { revision, image });
  if (publishedCovers.size > 30) publishedCovers.delete(publishedCovers.keys().next().value!);
  return image;
}

/** 只保留仍挂载且上次下载失败的卡片；手动同步时与最新列表核对后重试。 */
const failedCoverReads = new Set<(items: TemplateSummary[], missing: Set<string>) => Promise<void> | undefined>();

/** 将共享下载结果交给卡片；卸载或更换版本即取消该卡片的回调和重试。 */
export function watchTemplateCover(id: string, revision: string, show: (image: Blob | null) => void) {
  const address = apiBase();
  let active = true;
  // 版本变化由卡片按新版本加载；只在同一服务、同一发布版本下重试原请求。
  const retry = (items: TemplateSummary[], missing: Set<string>) => address === apiBase() && items.some(item =>
    item.template_id === id && item.cover?.url && item.cover.publishedRevision === revision) ? load(missing) : undefined;
  /** 同一张封面在多个陈列区失败时，readTemplateCover 会合并它们的请求。 */
  function load(missing?: Set<string>) {
    return readTemplateCover(id, revision).then(image => {
      if (active) { failedCoverReads.delete(retry); show(image); }
    }, error => {
      if (error instanceof MissingCoverObject) missing?.add(id);
      if (active) { failedCoverReads.add(retry); show(null); }
    });
  }
  void load();
  return () => { active = false; failedCoverReads.delete(retry); };
}

/** 手动同步等待当前封面下载及失败卡片的重读；只将存储确认缺失的对象交给补图流程。 */
export async function syncLoadedCovers(items: TemplateSummary[]) {
  const missing = new Set<string>();
  const reads = items.flatMap(item => {
    const cached = publishedCovers.get(apiBase() + "/" + item.template_id);
    if (!cached || !item.cover?.url) return [];
    const image = cached.revision === item.cover.publishedRevision ? cached.image : readTemplateCover(item.template_id, item.cover.publishedRevision);
    return [image.catch(error => { if (error instanceof MissingCoverObject) missing.add(item.template_id); })];
  });
  await Promise.all([...reads, ...Array.from(failedCoverReads, retry => retry(items, missing))]);
  return missing;
}

/** 上传失败只暂存该修订的截图；同一模板只保留最新一次，应用重启后由真实预览重新截取。 */
const coverBlobs = new Map<string, { revision: string; blob: Blob; refresh: boolean }>();

/** 重传同版本截图；取帧前若已从详情取得成功发布的封面，直接交给画廊。 */
export async function publishCover(id: string, revision: string, capture: () => Promise<Blob | CoverInfo>, refresh = false, signal?: AbortSignal) {
  const key = apiBase() + "/" + id;
  let item = coverBlobs.get(key);
  if (item?.revision !== revision) {
    if (item) coverBlobs.delete(key);
    const captured = await capture();
    signal?.throwIfAborted();
    if ("publishedRevision" in captured) return captured;
    item = { revision, blob: captured, refresh };
  }
  if (refresh) item.refresh = true;
  signal?.throwIfAborted();
  coverBlobs.set(key, item);
  const body = new FormData();
  body.set("file", item.blob, "cover.png");
  if (item.refresh) body.set("refresh", "true");
  const response = await cloud((signal) => cloudRequest(`/template/${id}/cover/${revision}`, "PUT", signal, body), signal);
  const result = await response.json() as { url: string; revision: string; source?: string };
  if (item.refresh && result.revision === revision) throw new Error("请更新本地服务后再刷新封面");
  if (coverBlobs.get(key) === item) coverBlobs.delete(key);
  return result;
}

/** 可在主页直接重传的内存图片；其余重试须回到对应模板的预览。 */
export function hasCoverBlob(id: string, revision: string) {
  return coverBlobs.get(apiBase() + "/" + id)?.revision === revision;
}
