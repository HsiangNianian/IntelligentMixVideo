/** Remotion Sprite 资产的客户端视图：发布目录条目，含分类、内容区域与可在模板里逐次修改的参数；摆放见 templates/model.ts 的 RemotionClip。 */
import type { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { AssetParameter, ContentBox } from "./parameters";

/** 公开目录条目；seconds 是生成时的预览长度，只作为片段的默认时长，渲染时长由摆放或总线决定。 */
export interface SpriteAsset {
  id: string;
  name: string;
  kind: SpriteKind;
  /** 发布它的字效会话版本；工作区据此找出本会话上次保存的资产，用来默认覆盖保存。旧服务端可能不返回。 */
  sourceVersionId?: string;
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
  /** 字效的固定行数：多行字效为 2～4，示例文字按行分别填写，字幕的连续切片逐行显示；缺省或 1 为单行。 */
  lines?: number;
  /** 双语字效（双语字幕）：两栏是同一句的中文与英文，模板里同时显示中英两种语言，只需绑定中文的文字对象。 */
  bilingual?: boolean;
}
