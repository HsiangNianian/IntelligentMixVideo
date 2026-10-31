/** AI 资产的分类、宿主变换与参数声明：与服务端 `categories.py`、`placement.py` 的规则一致，模板编辑据此生成控件并在本地校验。 */
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

/**
 * 模板编辑里开放给这次摆放的变换字段：字效只调位置，滤镜与转场只调强度，视频特效调全部。
 * 其余字段（例如字效的缩放）服务端契约仍允许；已有覆盖照常生效，但不在这里新增。
 */
export function instanceTransformFields(kind: SpriteKind): TransformField[] {
  return kind === SpriteKind.TEXT ? ["x", "y"] : transformFields(kind);
}

/**
 * 模板逐次设置的关键词参数：关键词本身与六个样式选项，与服务端 `categories.TEXT_KEYWORDS` 一致。字效自带的其他关键词
 * 效果参数（如 keywordEffectColor）属于资产本身，只在字效工作区修改。
 */
export const TEMPLATE_KEYWORD_PARAMETERS = ["keywords", "keywordBold", "keywordItalic", "keywordUnderline", "keywordStrikeout", "keywordColor", "keywordSize"];

/**
 * 字效的排版参数：基准字号、单栏的字体与字重、双栏的排布（上下排列或重叠）、栏间距与逐栏字体、字重、字号比例（名称与服务端 `categories.FIXED_TITLES` 一致）。
 * 字效工作区的「配置」组与模板编辑的「外观与位置」共用这份清单，两处能调的排版项相同。
 */
export const TEXT_LAYOUT_PARAMETERS = [
  "fontSize", "fontFamily", "fontWeight", "tierLayout", "lineGap", "translationGap",
  ...[1, 2, 3, 4].flatMap((tier) => [`line${tier}SizeRatio`, `line${tier}FontFamily`, `line${tier}FontWeight`]),
];

/**
 * 字效特效的配置参数：第 k 个特效的开关 `effect{k}`、作用位置 `effect{k}Target`、入场与出场等的时长 `effect{k}Seconds`（秒）、
 * 入场与下栏出现推迟开始的 `effect{k}Delay`（秒）及逐字位置上各字错开的程度 `effect{k}Stagger`（0～1），名称与服务端
 * `categories.EFFECT_PARAMETER` 一致。字效工作区单列为「特效」组，模板编辑按摆放调整。
 */
export const EFFECT_PARAMETER = /^effect[1-9][0-9]*(?:Target|Seconds|Delay|Stagger)?$/;

/**
 * 双语字效（双语字幕）的英文文字参数，与服务端 `categories.BILINGUAL_PROP` 一致：上栏是中文、下栏是同一句的英文，
 * 模板把它按所绑定文字对象的英文（菜单里开启中英双语）填给资产，合成时由切片译文注入。
 */
export const BILINGUAL_PROP = "textEn";

/** 内容组数：双语每组有原文和译文两行；普通字效按其声明的行数填写。 */
export function contentLines(asset: { lines?: number; bilingual?: boolean } | undefined): number {
  const lines = asset?.lines ?? 1;
  return asset?.bilingual ? Math.max(1, Math.floor(lines / 2)) : lines;
}

/**
 * 模板编辑里开放的资产参数：字效的排版参数、特效的开关、位置与时长以及关键词样式，以及自由组件按分组（`group`）声明的样式参数；
 * 组合字效的颜色、特效与关键词效果的外观等属于资产本身，回字效工作区改出新版本。已有覆盖不受影响。
 */
export function isInstanceParameter(kind: SpriteKind, key: string, group = ""): boolean {
  return !!group || (kind === SpriteKind.TEXT && (TEXT_LAYOUT_PARAMETERS.includes(key) || TEMPLATE_KEYWORD_PARAMETERS.includes(key) || EFFECT_PARAMETER.test(key)));
}

/** 画布像素换算为 0～100 的百分比（一位小数），与 IMS 文字位置同样的表示。 */
export function pixelsToPercent(pixels: number, total: number): number {
  return Math.round((pixels / total) * 1000) / 10;
}

/** 0～100 的百分比换算为画布像素（两位小数）。 */
export function percentToPixels(percent: number, total: number): number {
  return Math.round((percent / 100) * total * 100) / 100;
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

/** 资产对外的一个参数：控件描述、是否可改、发布默认值，以及自由组件样式参数的面板分组（空串表示不分组）。 */
export interface AssetParameter {
  field: ParameterField;
  editable: boolean;
  defaultValue: ParameterValue;
  group?: string;
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
  const group = parameter.group ? { group: parameter.group } : {};
  switch (value.case) {
    case "stringListValue":
      return { field: { ...base, kind: "keywords" }, editable, defaultValue: [...value.value.values], ...group };
    case "stringValue":
      return { field: { ...base, kind: parameter.format === SpriteParameterFormat.COLOR ? "color" : "string" }, editable, defaultValue: value.value, ...group };
    case "boolValue":
      return { field: { ...base, kind: "boolean" }, editable, defaultValue: value.value, ...group };
    case "numberValue":
      return { field: { ...base, kind: parameter.format === SpriteParameterFormat.INTEGER ? "integer" : "number" }, editable, defaultValue: value.value, ...group };
    default:
      return null;
  }
}
