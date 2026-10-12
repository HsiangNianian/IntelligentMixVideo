/** Remotion Sprite 资产的客户端视图：发布目录条目；资产本身不可配置，模板内的摆放见 templates/model.ts 的 RemotionClip。 */
import type { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";

/** 公开目录条目；seconds 是资产自身的播放时长，片段摆放时直接使用。 */
export interface SpriteAsset {
  id: string;
  name: string;
  kind: SpriteKind;
  seconds: number;
  /** 发布画布宽高比，叠加预览按 Cover 方式铺满模板画面。 */
  aspect: number;
  /** 发布画布；叠加渲染要求同一模板里所有片段的宽、高和帧率完全一致。 */
  width: number;
  height: number;
  fps: number;
}
