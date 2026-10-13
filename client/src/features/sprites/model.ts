/** Remotion Sprite 资产的客户端视图：发布目录条目，含分类、内容区域与可在模板里逐次修改的参数；摆放见 templates/model.ts 的 RemotionClip。 */
import type { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { AssetParameter, ContentBox } from "./parameters";

/** 公开目录条目；seconds 是生成时的预览长度，只作为片段的默认时长，渲染时长由摆放或总线决定。 */
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
  /** 宿主变换的原点所在区域：位置是它的中心，缩放和旋转绕它进行。 */
  box: ContentBox;
  /** 可见的发布参数（可修改或只读），按发布顺序。 */
  parameters: AssetParameter[];
}
