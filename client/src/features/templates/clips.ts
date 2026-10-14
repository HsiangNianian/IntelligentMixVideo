/**
 * 模板里 Remotion 片段的编辑操作：纯函数，返回新草稿且不修改输入。
 * 片段按资产分类占用模板对象，与 IMS 对象共用名额：字效作用于标题或底部字幕、转场占用视频交界处、视频特效占用唯一的 VFX，滤镜可以多个。
 */
import type { ParameterValue } from "@/components/ParameterControl";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import type { ClipTransform, TransformField } from "@/features/sprites/parameters";
import { textRoles, type Draft, type RemotionClip, type TextRole } from "./model";
import type { ResolvedTimeline } from "./tracks";
import { compactTransitions, nextTransitionBoundary } from "./transitions";

/** 片段与 IMS 对象共用的轨道上限，与服务端和本地文件一致。 */
const maxTracks = 100;

/** 片段在模板里占用的对象，与 IMS 对象的时间轴行一致。 */
export type ClipSlot = "title" | "subtitle" | "transition" | "vfx" | "filter";

/** 字效作用于文字时，这两个参数由该文字对象提供（合成时由总线填入），摆放不再单独覆盖。 */
export const BOUND_TEXT_PARAMETERS = ["text", "keywords"];

/** 画布描述，用于提示：宽×高 @ 帧率。 */
function canvasLabel(asset: SpriteAsset): string {
  return `${asset.width}×${asset.height} @ ${asset.fps} FPS`;
}

/** 片段占用的对象：已保存的绑定优先，否则按资产分类；字效尚未选择文字对象或目录未读到资产时返回 undefined。 */
export function clipSlot(clip: RemotionClip, asset?: SpriteAsset): ClipSlot | undefined {
  if (clip.text_target) return clip.text_target;
  if (clip.transition_boundary !== undefined) return "transition";
  if (!asset || asset.kind === SpriteKind.TEXT) return undefined;
  return asset.kind === SpriteKind.FILTER_OVERLAY ? "filter" : asset.kind === SpriteKind.TRANSITION_OVERLAY ? "transition" : "vfx";
}

/** 交界处是否已被 IMS 转场或其他 Remotion 转场占用。 */
export function boundaryTaken(draft: Draft, boundary: number, except?: string): boolean {
  return draft.tracks.some((track) => track.target === "transition" && track.transition_boundary === boundary)
    || draft.clips.some((clip) => clip.id !== except && clip.transition_boundary === boundary);
}

/** VFX 名额是否已被 IMS 画面特效或 Remotion 视频特效占用；`known` 用于查找片段的资产分类。 */
export function vfxTaken(draft: Draft, known: SpriteAsset[], except?: string): boolean {
  return draft.tracks.some((track) => track.target === "vfx")
    || draft.clips.some((clip) => clip.id !== except && clipSlot(clip, known.find((item) => item.id === clip.sprite_id)) === "vfx");
}

/** 已作用于该文字对象的 Remotion 字效。 */
function textClip(draft: Draft, role: TextRole, except?: string): RemotionClip | undefined {
  return draft.clips.find((clip) => clip.id !== except && clip.text_target === role);
}

/**
 * 添加一次摆放并按分类占用模板对象：字效作用于 `role`（当前选择的文字对象），转场追加到统一列表，视频特效占用 VFX 名额；
 * 名额已被占用时拒绝并说明原因，占用文字或交界处的片段起点与时长由 `timeClips` 按所占对象计算。
 * 叠加渲染要求所有片段的画布尺寸与帧率相同，所以与已添加片段画布不同的资产也在这里拒绝；`known` 是用来查找已添加片段资产的目录，服务端保存时仍会复核。
 */
export function addClip(draft: Draft, asset: SpriteAsset, known: SpriteAsset[], role: TextRole = "title"): Draft {
  if (draft.tracks.length + draft.clips.length >= maxTracks) throw new Error("轨道数量不能超过 100");
  const clash = draft.clips
    .map((clip) => known.find((item) => item.id === clip.sprite_id))
    .find((item) => item && (item.width !== asset.width || item.height !== asset.height || item.fps !== asset.fps));
  if (clash) throw new Error(`「${asset.name}」的画布（${canvasLabel(asset)}）与已添加的「${clash.name}」（${canvasLabel(clash)}）不一致，叠加视频要求同一模板里的资产画布尺寸和帧率相同`);
  const clip: RemotionClip = {
    id: `sprite-${crypto.randomUUID()}`,
    sprite_id: asset.id,
    start: 0,
    duration: asset.seconds > 0 ? asset.seconds : null,
    parameters: {},
    transform: {},
  };
  if (asset.kind === SpriteKind.TEXT) {
    if (role === "bubble") throw new Error("Remotion 字效只能作用于标题或底部字幕，请先在左侧选择标题或字幕");
    const existing = textClip(draft, role);
    if (existing) throw new Error(`${textRoles[role]}已有 Remotion 字效，请先移除再添加`);
    clip.text_target = role;
  } else if (asset.kind === SpriteKind.TRANSITION_OVERLAY) {
    clip.transition_boundary = nextTransitionBoundary(draft);
  } else if (asset.kind !== SpriteKind.FILTER_OVERLAY && vfxTaken(draft, known)) {
    throw new Error("画面特效最多一个，Remotion 视频特效也占用这个名额，请先移除已有的画面特效");
  }
  return { ...draft, clips: [...draft.clips, clip] };
}

/** 改变字效作用的文字对象或转场所在的交界处；目标已被占用时拒绝。作用于文字后，文字与关键词由该对象提供，清除对应的参数覆盖。 */
export function bindClip(draft: Draft, id: string, binding: { text_target: "title" | "subtitle" } | { transition_boundary: number }): Draft {
  if ("text_target" in binding && textClip(draft, binding.text_target, id))
    throw new Error(`${textRoles[binding.text_target]}已有 Remotion 字效，请先移除`);
  if ("transition_boundary" in binding && boundaryTaken(draft, binding.transition_boundary, id))
    throw new Error(`交界处 ${binding.transition_boundary} 已有转场，请先移除`);
  return { ...draft, clips: draft.clips.map((clip) => {
    if (clip.id !== id) return clip;
    if (!("text_target" in binding)) return { ...clip, ...binding };
    const parameters = Object.fromEntries(Object.entries(clip.parameters).filter(([key]) => !BOUND_TEXT_PARAMETERS.includes(key)));
    return { ...clip, ...binding, parameters };
  }) };
}

/** 保存前检查片段占用的对象互不冲突；分类是否相符与 VFX 名额需要资产分类，由服务端复核。 */
export function validateClipSlots(draft: Draft): void {
  const texts = new Set<string>();
  for (const clip of draft.clips) {
    if (clip.text_target && clip.transition_boundary !== undefined) throw new Error("Remotion 片段不能同时作用于文字和视频交界处");
    if (clip.text_target) {
      if (texts.has(clip.text_target)) throw new Error("每个标题或字幕最多一个 Remotion 字效");
      texts.add(clip.text_target);
    }
    if (clip.transition_boundary !== undefined && boundaryTaken(draft, clip.transition_boundary, clip.id)) throw new Error("每个视频交界处最多一个转场");
  }
}

/** 预览中没有有效时间的字效与转场隐藏，列表标为未应用。 */
export function hiddenTextClips(draft: Draft, resolved: ResolvedTimeline): Set<string> {
  return new Set(draft.clips.filter((clip) => {
    if (clip.transition_boundary !== undefined) {
      const transition = resolved.transitions[clip.transition_boundary - 1];
      return !transition || transition.end <= transition.start;
    }
    const text = clip.text_target && draft.tracks.find((track) => track.target === clip.text_target);
    const shown = text ? resolved.tracks[text.id] : undefined;
    return !!text && !(shown && shown.end > shown.start);
  }).map((clip) => clip.id));
}

/**
 * 把本次预览算出的秒数写入占用对象的片段，供右侧列表、时间轴、叠加预览和保存数据使用：字效采用所作用文字的显示区间，
 * Remotion 转场预览采用计算后的区间，保存保留原有时长；所作用文字不显示时保留原值（见 `hiddenTextClips`）。合成时按实际素材重新计算；没有变化的片段与列表保持原引用。
 */
export function timeClips(draft: Draft, resolved: ResolvedTimeline, purpose: "preview" | "save" = "preview"): Draft {
  const round = (seconds: number) => Math.round(seconds * 1000) / 1000;
  const clips = draft.clips.map((clip) => {
    let timing: Pick<RemotionClip, "start" | "duration"> | undefined;
    const text = clip.text_target && draft.tracks.find((track) => track.target === clip.text_target);
    const shown = text ? resolved.tracks[text.id] : undefined;
    if (shown && shown.end > shown.start) timing = { start: round(shown.start), duration: round(shown.end - shown.start) };
    else if (purpose === "preview" && clip.transition_boundary !== undefined && resolved.transitions[clip.transition_boundary - 1]) {
      const transition = resolved.transitions[clip.transition_boundary - 1];
      timing = { start: round(transition.start), duration: round(transition.end - transition.start) };
    }
    return !timing || (timing.start === clip.start && timing.duration === clip.duration) ? clip : { ...clip, ...timing };
  });
  return clips.every((clip, index) => clip === draft.clips[index]) ? draft : { ...draft, clips };
}

/** 调整起点（秒，毫秒精度）；片段只能整体移动，时长不变。 */
export function moveClip(draft: Draft, id: string, start: number): Draft {
  return { ...draft, clips: draft.clips.map((clip) => clip.id === id ? { ...clip, start: Math.round(start * 1000) / 1000 } : clip) };
}

/** 移除一次摆放，其余片段的先后（叠放层级）不变。 */
export function removeClip(draft: Draft, id: string): Draft {
  const next = { ...draft, clips: draft.clips.filter((clip) => clip.id !== id) };
  return draft.clips.some((clip) => clip.id === id && clip.transition_boundary !== undefined) ? compactTransitions(next) : next;
}

/** 修改这次摆放的一个参数；undefined 表示恢复资产默认值。 */
export function setClipParameter(draft: Draft, id: string, key: string, value: ParameterValue | undefined): Draft {
  return { ...draft, clips: draft.clips.map((clip) => {
    if (clip.id !== id) return clip;
    const parameters = { ...clip.parameters };
    if (value === undefined) delete parameters[key];
    else parameters[key] = value;
    return { ...clip, parameters };
  }) };
}

/** 合并宿主变换；字段为 undefined 时恢复默认值。 */
export function setClipTransform(draft: Draft, id: string, patch: Partial<Record<TransformField, number | undefined>>): Draft {
  return { ...draft, clips: draft.clips.map((clip) => {
    if (clip.id !== id) return clip;
    const transform: ClipTransform = { ...clip.transform };
    for (const [name, value] of Object.entries(patch) as [TransformField, number | undefined][]) {
      if (value === undefined) delete transform[name];
      else transform[name] = value;
    }
    return { ...clip, transform };
  }) };
}
