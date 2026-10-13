/** 核心测试共用的固定目录和模板响应；纯本地数据，不加载 SDK 或数据库。 */
import { create, toBinary } from "@bufbuild/protobuf";
import { timestampFromDate } from "@bufbuild/protobuf/wkt";
import {
  GetTemplateResponseSchema, ListTemplatesResponseSchema, SaveTemplateResponseSchema,
  TemplateRecordSchema, type TemplateRecord,
} from "@/generated/imv/template/v1/template_pb";
import { defaultEditor, newDraft, type Draft, type EffectDraft, type EffectAsset, type Template, type TemplateSummary, type PreviewMedia } from "@/features/templates/model";
import { previewContext, resolveTemplate, trackEditor } from "@/features/templates/tracks";
import { buildTimeline } from "@/features/templates/timeline";

/** 固定长度输入用于纯时间计算，测试不执行媒体网络读取。 */
export const sampleMedia: PreviewMedia = [
  { url: "https://media.example/one.mp4", duration: 10, width: 1080, height: 1920 },
  { url: "https://media.example/two.mp4", duration: 10, width: 1080, height: 1920 },
  { url: "https://media.example/three.mp4", duration: 10, width: 1080, height: 1920 },
];

/** 使用真实计算和 SDK 转换函数，验证同一输入经过完整转换的结果。 */
export function buildTestTimeline(draft: Draft, assets: EffectAsset[], media = sampleMedia, order = [0, 1, 2]) {
  return buildTimeline(draft, assets, media, resolveTemplate(draft, previewContext(media, order)), order);
}

/** 从有效模板提取协议规定的列表摘要。 */
export function templateSummary(template: Template): TemplateSummary {
  return { template_id: template.template_id, name: template.name, description: template.description,
    created_at: template.created_at, updated_at: template.updated_at,
    title: template.tracks.find((track) => track.target === "title")?.editor.title ?? "",
    subtitle: template.tracks.find((track) => track.target === "subtitle")?.editor.subtitle ?? "",
    track_count: template.tracks.length + (template.clips?.length ?? 0), validation_error: "" };
}

/** 建立独立的对象参数，供参数面板和效果函数测试使用。 */
export function effectDraft(): EffectDraft {
  return { editor: { ...defaultEditor }, transition_duration_seconds: 1 };
}

/** 显式添加两个基础文字对象，供时间规则与预览测试使用。 */
export function sampleDraft(): Draft {
  return { ...newDraft(), tracks: (["title", "subtitle"] as const).map((target) => ({
    id: target, target, start_mode: "seconds", start: 0, duration: null,
    editor: trackEditor(defaultEditor, target),
  })) };
}

/** 两个代表效果足以验证文字动画与片段转场的转换。 */
export const catalog: EffectAsset[] = [
  {
    id: "in/fade_in", category: "in", name: "淡入", effect_id: "fade_in",
    parameters: { AaiMotionInEffect: "fade_in" }, preview_url: "",
  },
  {
    id: "transition/normal/directional", category: "transition/normal", name: "方向转场",
    effect_id: "directional", parameters: { SubType: "directional" }, preview_url: "",
  },
];

/** 返回独立模板，测试可修改草稿而不会污染其他用例。 */
export function savedTemplate(): Template {
  const draft = sampleDraft();
  draft.tracks[0].editor.titleIn = "in/fade_in";
  return {
    ...draft, name: "已有模板",
    template_id: "00000000-0000-4000-8000-000000000001",
    effect_ids: ["in/fade_in"], effects: [catalog[0]],
    created_at: "2026-01-01T00:00:00.000Z", updated_at: "2026-01-01T00:00:00.000Z",
  };
}

/** 将固定模板编码为服务端返回的生成消息。 */
function templateRecord(template: Template): TemplateRecord {
  return create(TemplateRecordSchema, {
    name: template.name,
    description: template.description,
    effectIds: template.effect_ids,
    transitionDurationSeconds: template.transition_duration_seconds,
    templateId: template.template_id,
    tracks: { tracks: [
      ...template.tracks.map((track) => ({
        id: track.id, target: track.target, startMode: track.start_mode,
        start: track.start, duration: track.duration ?? undefined, transitionBoundary: track.transition_boundary, editor: track.editor,
      })),
      // 服务端把 Remotion 片段作为 remotion 轨道返回，editor 只带参数覆盖与宿主变换（没有时为空消息）。
      ...(template.clips ?? []).map((clip) => ({
        id: clip.id, target: "remotion", startMode: "seconds", start: clip.start,
        duration: clip.duration ?? undefined, spriteId: clip.sprite_id,
        editor: Object.keys(clip.parameters ?? {}).length || Object.keys(clip.transform ?? {}).length ? { remotion: {
          parameters: Object.entries(clip.parameters ?? {}).map(([key, value]) => ({
            key, ...(Array.isArray(value) ? { stringListValue: { values: value } } : typeof value === "string" ? { stringValue: value } : typeof value === "number" ? { numberValue: value } : { boolValue: value }),
          })),
          transform: Object.keys(clip.transform ?? {}).length ? clip.transform : undefined,
        } } : {},
      })),
    ] },
    effects: template.effects.map((effect) => ({
      id: effect.id, category: effect.category, name: effect.name,
      effectId: effect.effect_id, parameters: effect.parameters, previewUrl: effect.preview_url,
    })),
    createdAt: timestampFromDate(new Date(template.created_at)),
    updatedAt: timestampFromDate(new Date(template.updated_at)),
  });
}

/** 产生真实 Protobuf 列表响应，供已有 HTTP 测试读取。 */
export function protobufListResponse(templates: Template[]): Response {
  const message = create(ListTemplatesResponseSchema, { templates: templates.map((template) => {
    const summary = templateSummary(template);
    return { templateId: summary.template_id, name: summary.name, description: summary.description,
      createdAt: timestampFromDate(new Date(summary.created_at)), updatedAt: timestampFromDate(new Date(summary.updated_at)),
      title: summary.title, subtitle: summary.subtitle, trackCount: summary.track_count, validationError: summary.validation_error };
  }) });
  return new Response(Uint8Array.from(toBinary(ListTemplatesResponseSchema, message)), {
    headers: { "Content-Type": "application/x-protobuf" },
  });
}

/** 产生真实 Protobuf 详情或保存响应，保留 HTTP 状态码。 */
export function protobufTemplateResponse(template: Template, kind: "get" | "save", status = 200): Response {
  const record = templateRecord(template);
  const schema = kind === "get" ? GetTemplateResponseSchema : SaveTemplateResponseSchema;
  const message = create(schema, { template: record });
  return new Response(Uint8Array.from(toBinary(schema, message)), {
    status, headers: { "Content-Type": "application/x-protobuf" },
  });
}
