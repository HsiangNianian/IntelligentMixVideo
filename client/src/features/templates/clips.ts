/** 模板里 Remotion 片段的编辑操作：纯函数，返回新草稿且不修改输入；`tracks` 引用保持不变，所以不会重建 SDK 预览。 */
import type { SpriteAsset } from "@/features/sprites/model";
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
