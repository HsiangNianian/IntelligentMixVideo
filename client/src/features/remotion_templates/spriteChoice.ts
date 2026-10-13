/** 保存到资产时的发布类型：默认取 Agent 判断的分类，用户可以改选；字效的文字与关键词字段名固定。 */
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { PublishChoice } from "@/features/sprites/api";
import type { SpriteKindName, Version } from "./model";

/** 发布时可选的类型；视频特效与转场在资产库里同属「转场与视频特效」。 */
export const PUBLISH_KINDS: { kind: SpriteKind; name: SpriteKindName; label: string }[] = [
  { kind: SpriteKind.TEXT, name: "text", label: "字效" },
  { kind: SpriteKind.FILTER_OVERLAY, name: "filter_overlay", label: "滤镜" },
  { kind: SpriteKind.VIDEO_OVERLAY, name: "video_overlay", label: "视频特效" },
  { kind: SpriteKind.TRANSITION_OVERLAY, name: "transition_overlay", label: "转场" },
];

/** Agent 记录的分类（`version.spec.sprite?.kind`）；没有记录的旧版本默认视频特效（服务端会要求先在聊天中改出新版本）。 */
export function defaultKind(agentKind: SpriteKindName | undefined): SpriteKind {
  return PUBLISH_KINDS.find((item) => item.name === agentKind)?.kind ?? SpriteKind.VIDEO_OVERLAY;
}

/** 按所选类型生成发布请求：字效固定 text 字段，声明了关键词参数时同时发布 keywords。 */
export function publishChoice(version: Version, kind: SpriteKind): PublishChoice {
  if (kind !== SpriteKind.TEXT) return { kind, textProp: "", keywordsProp: "" };
  const keywords = (version.spec.sprite?.bindings ?? []).some((item) => item.name === "keywords");
  return { kind, textProp: "text", keywordsProp: keywords ? "keywords" : "" };
}
