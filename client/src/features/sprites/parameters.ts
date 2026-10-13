/** Remotion 资产的分类、宿主变换与参数声明：与服务端 `categories.py`、`placement.py` 的规则一致，模板编辑据此生成控件并在本地校验。 */
import { OperatorAccess, SpriteKind, SpriteParameterFormat, type SpriteParameter } from "@/generated/imv/sprite/v1/sprite_pb";
import type { ParameterField, ParameterValue } from "@/components/ParameterControl";

/** 资产库的三个分类；视频特效与转场合为一类，未指定类型的历史资产也归入其中。 */
export const CATEGORIES = [
  { id: "text", label: "字效", kinds: [SpriteKind.TEXT] },
  { id: "filter", label: "滤镜", kinds: [SpriteKind.FILTER_OVERLAY] },
  { id: "effect", label: "转场与视频特效", kinds: [SpriteKind.VIDEO_OVERLAY, SpriteKind.TRANSITION_OVERLAY, SpriteKind.UNSPECIFIED] },
] as const;
export type CategoryId = (typeof CATEGORIES)[number]["id"];

/** 资产所属的分类。 */
export function categoryOf(kind: SpriteKind): CategoryId {
  return CATEGORIES.find((item) => (item.kinds as readonly SpriteKind[]).includes(kind))?.id ?? "effect";
}

/** 宿主变换的字段：x、y 是内容区域中心在画布上的坐标。 */
export type TransformField = "x" | "y" | "scale" | "rotation" | "opacity";
/** 一次摆放的宿主变换；未设置的字段取默认值。 */
export type ClipTransform = Partial<Record<TransformField, number>>;
/** 内容区域，单位为画布像素。 */
export interface ContentBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 滤镜与转场覆盖整块画布，只能调整不透明度（强度）。 */
export function transformFields(kind: SpriteKind): TransformField[] {
  return kind === SpriteKind.FILTER_OVERLAY || kind === SpriteKind.TRANSITION_OVERLAY
    ? ["opacity"]
    : ["x", "y", "scale", "rotation", "opacity"];
}

/** 宿主变换各字段的控件：范围与服务端一致，默认值来自内容区域中心。 */
export function transformField(field: TransformField, kind: SpriteKind, width: number, height: number): ParameterField & { fallback: (box: ContentBox) => number } {
  const full = transformFields(kind).length === 1;
  return {
    x: { key: "x", label: "横向位置", kind: "number", minimum: 0, maximum: width, fallback: (box: ContentBox) => box.x + box.width / 2 },
    y: { key: "y", label: "纵向位置", kind: "number", minimum: 0, maximum: height, fallback: (box: ContentBox) => box.y + box.height / 2 },
    scale: { key: "scale", label: "缩放", kind: "number", minimum: 0.25, maximum: 4, fallback: () => 1 },
    rotation: { key: "rotation", label: "旋转（度）", kind: "number", minimum: -180, maximum: 180, fallback: () => 0 },
    opacity: { key: "opacity", label: full ? "强度" : "不透明度", kind: "number", minimum: 0, maximum: 1, fallback: () => 1 },
  }[field] as ParameterField & { fallback: (box: ContentBox) => number };
}

/** 资产对外的一个参数：控件描述、是否可改与发布默认值。 */
export interface AssetParameter {
  field: ParameterField;
  editable: boolean;
  defaultValue: ParameterValue;
}

/** 把发布时的参数声明转换为控件；内部参数与无法识别的值不展示。 */
export function assetParameter(parameter: SpriteParameter): AssetParameter | null {
  const value = parameter.defaultValue?.value;
  const visible = parameter.access === OperatorAccess.VISIBLE_EDITABLE || parameter.access === OperatorAccess.VISIBLE_READ_ONLY;
  if (!value?.case || !visible) return null;
  const options = parameter.allowedValues.map((item) => item.value.value).filter((item): item is string | number | boolean => item !== undefined && typeof item !== "object");
  const base = {
    key: parameter.key,
    label: parameter.label || parameter.key,
    ...(options.length ? { options } : {}),
    ...(parameter.minimum !== undefined ? { minimum: parameter.minimum } : {}),
    ...(parameter.maximum !== undefined ? { maximum: parameter.maximum } : {}),
    ...(parameter.minLength !== undefined ? { minLength: parameter.minLength } : {}),
    ...(parameter.maxLength !== undefined ? { maxLength: parameter.maxLength } : {}),
    ...(parameter.maxItems !== undefined ? { maxItems: parameter.maxItems } : {}),
  };
  const editable = parameter.access === OperatorAccess.VISIBLE_EDITABLE;
  switch (value.case) {
    case "stringListValue":
      return { field: { ...base, kind: "keywords" }, editable, defaultValue: [...value.value.values] };
    case "stringValue":
      return { field: { ...base, kind: parameter.format === SpriteParameterFormat.COLOR ? "color" : "string" }, editable, defaultValue: value.value };
    case "boolValue":
      return { field: { ...base, kind: "boolean" }, editable, defaultValue: value.value };
    case "numberValue":
      return { field: { ...base, kind: parameter.format === SpriteParameterFormat.INTEGER ? "integer" : "number" }, editable, defaultValue: value.value };
    default:
      return null;
  }
}

/** 把以点号路径记录的覆盖展开为嵌套对象，供播放器合并到默认配置之上。 */
export function overrideValues(parameters: Record<string, ParameterValue>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(parameters)) {
    const path = key.split(".");
    let target = result;
    for (const part of path.slice(0, -1)) target = (target[part] ??= {}) as Record<string, unknown>;
    target[path.at(-1)!] = value;
  }
  return result;
}
