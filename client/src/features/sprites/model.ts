/** 已发布 Remotion 资产的客户端视图；发布结果不依赖模板或项目。 */
import type { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";

/** 发布资产是固定画布和时长的不可变片段。 */
export interface SpriteAsset {
  id: string;
  name: string;
  kind: SpriteKind;
  seconds: number;
  aspect: number;
}
