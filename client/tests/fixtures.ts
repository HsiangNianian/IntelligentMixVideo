/** 核心测试共用的固定目录和模板响应；纯本地数据，不加载 SDK 或数据库。 */
import { defaultEditor, newDraft, type Draft, type EffectDraft, type EffectAsset, type Template } from "@/features/templates/model";
import { trackEditor } from "@/features/templates/tracks";

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
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  };
}
