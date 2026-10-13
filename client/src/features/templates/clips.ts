/** 模板里 Remotion 片段的编辑操作：纯函数，返回新草稿且不修改输入；`tracks` 引用保持不变，所以不会重建 SDK 预览。 */
import type { ParameterValue } from "@/components/ParameterControl";
import type { SpriteAsset } from "@/features/sprites/model";
import type { ClipTransform, TransformField } from "@/features/sprites/parameters";
import type { Draft, RemotionClip } from "./model";

/** 片段与 IMS 对象共用的轨道上限，与服务端和本地文件一致。 */
const maxTracks = 100;

/** 画布描述，用于提示：宽×高 @ 帧率。 */
function canvasLabel(asset: SpriteAsset): string {
  return `${asset.width}×${asset.height} @ ${asset.fps} FPS`;
}

/**
 * 添加一次摆放：从模板开头播放，持续整个资产长度；同一个资产可以重复添加，每次都是独立的摆放。
 * 叠加渲染要求所有片段的画布尺寸与帧率相同，所以与已添加片段画布不同的资产在这里就拒绝；`known` 是用来查找已添加片段资产的目录，服务端保存时仍会复核。
 */
export function addClip(draft: Draft, asset: SpriteAsset, known: SpriteAsset[]): Draft {
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
  return { ...draft, clips: [...draft.clips, clip] };
}

/** 调整起点（秒，毫秒精度）；片段只能整体移动，时长不变。 */
export function moveClip(draft: Draft, id: string, start: number): Draft {
  return { ...draft, clips: draft.clips.map((clip) => clip.id === id ? { ...clip, start: Math.round(start * 1000) / 1000 } : clip) };
}

/** 移除一次摆放，其余片段的先后（叠放层级）不变。 */
export function removeClip(draft: Draft, id: string): Draft {
  return { ...draft, clips: draft.clips.filter((clip) => clip.id !== id) };
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
