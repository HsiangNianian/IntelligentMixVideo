/** 一键保存到资产时自动确定发布类型与文字字段：固定效果不需要用户选择。 */
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { PublishChoice } from "@/features/sprites/api";
import type { Control, Version } from "./model";

/** 递归列出字符串参数的点号路径，与服务端发布校验的写法一致。 */
function stringPaths(properties: Record<string, Control>, prefix = ""): string[] {
  return Object.entries(properties).flatMap(([name, definition]) => {
    if (name.includes(".")) return [];
    const path = `${prefix}${name}`;
    if (definition.type === "object" && definition.properties) return stringPaths(definition.properties, `${path}.`);
    return definition.type === "string" ? [path] : [];
  });
}

/** 新生成的组合版本发布为视频叠加；旧版本沿用创建时固定的类型，文字类自动取标题/文字字段。 */
export function publishChoice(version: Version): PublishChoice {
  switch (version.spec.sprite_kind) {
    case "filter_overlay": return { kind: SpriteKind.FILTER_OVERLAY, textProp: "", keywordsProp: "" };
    case "video_overlay": return { kind: SpriteKind.VIDEO_OVERLAY, textProp: "", keywordsProp: "" };
    case "transition_overlay": return { kind: SpriteKind.TRANSITION_OVERLAY, textProp: "", keywordsProp: "" };
    case "text":
    case "subtitle": {
      const strings = stringPaths(version.candidate.config_schema.properties);
      const textProp = strings.find((path) => /(^|\.)(text|title)$/i.test(path)) ?? strings[0];
      if (!textProp) throw new Error("该版本没有可作为文字的参数，无法保存到资产");
      return { kind: SpriteKind.TEXT, textProp, keywordsProp: "" };
    }
    default: return { kind: SpriteKind.VIDEO_OVERLAY, textProp: "", keywordsProp: "" };
  }
}
