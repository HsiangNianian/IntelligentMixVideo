/** Remotion 服务契约与参数解释；只保存当前会话和已验收模板，不暴露内部候选。 */
import { HEX_COLOR, type ParameterField } from "@/components/ParameterControl";
import { TEMPLATE_KEYWORD_PARAMETERS, TEXT_LAYOUT_PARAMETERS } from "@/features/sprites/parameters";

/** 第一版参数只支持字符串、有限数值和布尔值。 */
export type Scalar = string | number | boolean;
/** PR76 参数允许嵌套 JSON；旧版本仍保留扁平标量。 */
export type JsonValue = Scalar | null | JsonValue[] | { [key: string]: JsonValue };
export type Values = Record<string, JsonValue>;
/** 宿主控制的画布与整帧时长，同时用于生成请求和成功版本展示。 */
export interface Composition {
  width: number;
  height: number;
  fps: number;
  duration_in_frames: number;
}
/** 解析后的 JSON 仍可能包含溢出数字；只接受有界、有限的 JSON 值。 */
export function isJsonValue(value: unknown, depth = 0): value is JsonValue {
  if (depth > 30) return false;
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(item => isJsonValue(item, depth + 1));
  return typeof value === "object" && Object.values(value).every(item => isJsonValue(item, depth + 1));
}

/** 已验收的扁平标量控件，路径绑定服务端目标规格。 */
export interface Control {
  type: "string" | "number" | "integer" | "boolean" | "object" | "array" | "null";
  title?: string;
  description?: string;
  enum?: Scalar[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  maxItems?: number;
  items?: Control;
  "x-imv-target"?: string;
  properties?: Record<string, Control>;
}
/** 效果分类：字效、滤镜、视频特效、转场（后两者在界面上合为「转场与视频特效」）。 */
export type SpriteKindName = "text" | "filter_overlay" | "video_overlay" | "transition_overlay";
/** 新会话首条消息附带的生成选择：预览时长（整帧）与「关键词字效」「双栏」两个开关。 */
export interface NewWork {
  durationInFrames: number;
  keywordEffect: boolean;
  twoTier: boolean;
}
/** 成功版本提供代码、参数默认值、控件和画布信息。 */
export interface Version {
  id: string;
  project_id: string;
  number: number;
  source: "agent" | "user_parameters";
  created_at: string;
  candidate: {
    tsx_code: string;
    default_config: Values;
    config_schema: { properties: Record<string, Control> };
  };
  spec: {
    schema_version?: "1" | "2";
    name: string;
    sprite_kind?: "text" | "subtitle" | "filter_overlay" | "video_overlay" | "transition_overlay" | "composition";
    composition: Composition;
    text_layers: { id: string; text: string }[];
    /** 组合版本的分类与顶层参数绑定，字效的文字组合另有各栏、关键词与特效的实例 ID（特效带中文名称）；更早的版本没有。 */
    sprite?: { kind?: SpriteKindName; bindings?: { name: string; title: string; targets?: { instance_id: string; parameter: string }[] }[]; text?: { tiers: string[]; keyword?: string; effects?: { instance_id: string; title: string }[] } };
  };
}

/** 参数面板里一个实例的名称：文字组合的一栏为「正文」或「上栏」「下栏」，关键词 Preset 为「关键词字效」，特效为「特效 · 名称」，其他实例显示实例 ID。 */
export function instanceTitle(version: Version, id: string): string {
  const block = version.spec.sprite?.text;
  const tier = block?.tiers.indexOf(id) ?? -1;
  if (block && tier >= 0) return block.tiers.length === 1 ? "正文" : ["上栏", "下栏"][tier];
  if (block?.keyword === id) return "关键词字效";
  const effect = block?.effects?.find((item) => item.instance_id === id);
  return effect ? `特效 · ${effect.title}` : `实例 · ${id}`;
}

/** 一个 Schema 属性对应的控件描述；对象、未约束的数组等无法逐项编辑的形状返回 null。 */
export function schemaField(key: string, control: Control): ParameterField | null {
  const base = { key, label: control.title || key };
  switch (control.type) {
    case "boolean":
      return { ...base, kind: "boolean" };
    case "number":
    case "integer":
      return { ...base, kind: control.type, options: control.enum as number[] | undefined, minimum: control.minimum, maximum: control.maximum };
    case "string":
      return {
        ...base,
        kind: control.pattern !== undefined && control.pattern === HEX_COLOR.source ? "color" : "string",
        options: control.enum as string[] | undefined,
        minLength: control.minLength,
        maxLength: control.maxLength,
        ...(control.pattern !== undefined && control.pattern !== HEX_COLOR.source ? { pattern: control.pattern } : {}),
      };
    case "array":
      return control.items?.type === "string" ? { ...base, kind: "keywords", maxItems: control.maxItems, maxLength: control.items.maxLength } : null;
    default:
      return null;
  }
}

/** 多行字效的行起点参数名，与服务端 `categories.LINE_STARTS_PROP` 一致。 */
const LINE_STARTS_KEY = "lineStartFrames";

/**
 * 字效的配置参数：名称、Schema 与中文标题由宿主固定（服务端 `categories.FIXED_TITLES`），是文字、字体与字号、双栏排版、
 * 关键词这些「写什么、怎么排」的设置；其余顶层参数是 Agent 为这个字效设计的样式（颜色、发光、描边、动画、关键词字效的外观）。
 */
const CONFIG_PARAMETERS = new Set(["text", ...TEXT_LAYOUT_PARAMETERS, ...TEMPLATE_KEYWORD_PARAMETERS, "keywordEffect"]);

/**
 * 组合版本的参数分组：顶层参数分为配置（见 `CONFIG_PARAMETERS`）与样式，以及各实例的内部参数；
 * 实例里有无法逐项编辑的形状时整体回退为 JSON。文字组合的字效改用 `textSections`。
 */
export function schemaGroups(properties: Record<string, Control>): {
  config: ParameterField[];
  style: ParameterField[];
  instances: { id: string; fields: ParameterField[] | null }[];
} {
  const config: ParameterField[] = [];
  const style: ParameterField[] = [];
  const instances: { id: string; fields: ParameterField[] | null }[] = [];
  // 多行字效每行出现的帧由宿主按字幕切片注入，不在参数面板编辑；它的项数就是文字的固定行数。
  const lines = properties[LINE_STARTS_KEY]?.maxItems;
  for (const [key, control] of Object.entries(properties)) {
    if (key === LINE_STARTS_KEY) continue;
    if (control.type !== "object") {
      const field = schemaField(key, control);
      if (field) (CONFIG_PARAMETERS.has(key) ? config : style).push(key === "text" && field.kind === "string" && lines && lines > 1 ? { ...field, lines } : field);
      else instances.push({ id: key, fields: null });
      continue;
    }
    const nested = Object.entries(control.properties ?? {}).map(([name, child]) => schemaField(name, child));
    instances.push({ id: key, fields: nested.length && nested.every(Boolean) ? (nested as ParameterField[]) : null });
  }
  return { config, style, instances };
}

/** 一个实例的内部参数：形状无法逐项编辑时 fields 为 null，回退为 JSON 文本域。 */
export interface InstanceFields {
  id: string;
  fields: ParameterField[] | null;
}

/** 「特效」里的一张卡片：名称、是否应用的开关（顶层布尔参数）、其余顶层参数（位置、时长与绑定在这个特效上的样式），以及特效实例的内部参数。 */
export interface EffectCard {
  id: string;
  title: string;
  toggle: ParameterField | null;
  fields: ParameterField[];
  instance: InstanceFields | null;
}

/**
 * 文字组合字效的两块参数。「文字」是文字本身：内容、字号、字体与字重、双栏排版、各栏外观（Agent 绑定在各栏上的颜色、描边、
 * 字间距等）、关键词选项，以及各栏 Preset 的内部参数（`instances`）。「特效」按特效列出卡片：开关 `effect{k}`、位置
 * `effect{k}Target`、时长 `effect{k}Seconds`、延迟 `effect{k}Delay`、绑定在这个特效实例上的样式参数及其内部参数；关键词字效（关键词 Preset）也是一张
 * 卡片，开关为 `keywordEffect`。顶层参数按绑定目标归入实例；没有文字组合的版本返回 null，沿用 `schemaGroups`。
 */
export function textSections(version: Version): {
  text: ParameterField[];
  keyword: ParameterField[];
  instances: InstanceFields[];
  effects: EffectCard[];
} | null {
  const block = version.spec.sprite?.text;
  if (!block) return null;
  const properties = version.candidate.config_schema.properties ?? {};
  const bindings = version.spec.sprite?.bindings ?? [];
  /** 一个实例的内部参数；不是对象 Schema 时为 null。 */
  const nested = (id: string): InstanceFields | null => {
    const control = properties[id];
    if (control?.type !== "object") return null;
    const fields = Object.entries(control.properties ?? {}).map(([name, child]) => schemaField(name, child));
    return { id, fields: fields.every(Boolean) ? (fields as ParameterField[]) : null };
  };
  const field = (key: string) => (properties[key] ? schemaField(key, properties[key]) : null);
  const carded = new Set<string>([LINE_STARTS_KEY]);
  /** 一张卡片：toggle 是开关参数名，keys 是位置、时长等宿主参数，绑定到 instance 的样式参数随后列出。 */
  const card = (instance: string, title: string, toggle: string, keys: string[]): EffectCard => {
    const names = [...keys, ...bindings.filter((item) => item.targets?.some((target) => target.instance_id === instance)).map((item) => item.name)];
    [toggle, ...names, instance].forEach((name) => carded.add(name));
    return { id: instance, title, toggle: field(toggle), fields: names.flatMap((name) => field(name) ?? []), instance: nested(instance) };
  };
  const effects = (block.effects ?? []).map((effect, index) =>
    card(effect.instance_id, properties[`effect${index + 1}`]?.title || effect.title, `effect${index + 1}`, [`effect${index + 1}Target`, `effect${index + 1}Seconds`, `effect${index + 1}Delay`]));
  if (block.keyword) effects.push(card(block.keyword, "关键词字效", "keywordEffect", []));
  const text: ParameterField[] = [];
  const keyword: ParameterField[] = [];
  const instances: InstanceFields[] = [];
  const lines = properties[LINE_STARTS_KEY]?.maxItems;
  for (const [key, control] of Object.entries(properties)) {
    if (carded.has(key)) continue;
    if (control.type === "object") {
      instances.push(nested(key)!);
      continue;
    }
    const item = schemaField(key, control);
    if (!item) instances.push({ id: key, fields: null });
    else if (TEMPLATE_KEYWORD_PARAMETERS.includes(key)) keyword.push(item);
    else text.push(key === "text" && item.kind === "string" && lines && lines > 1 ? { ...item, lines } : item);
  }
  return { text, keyword, instances, effects };
}
/** 服务端隔离类型检查返回的零基行列；与 LSP 一致，character 以 UTF-16 计。 */
export interface TextPosition {
  line: number;
  character: number;
}
/** 诊断范围结束位置排他。 */
export interface TextRange {
  start: TextPosition;
  end: TextPosition;
}
/** 一条真实诊断：契约检查或 TypeScript 语言服务，不包含模型推测。 */
export interface Diagnostic {
  source: "contract" | "lsp";
  severity: "error" | "warning" | "information" | "hint";
  message: string;
  file?: string;
  code?: string;
  range?: TextRange;
  field?: string;
}
/** 成功版本的只读诊断结论；未通过时可能只有失败检查而没有定位信息。 */
export interface DiagnosticsReport {
  passed: boolean;
  diagnostics: Diagnostic[];
}

/** 公开执行状态只含结果引用、追问和简短错误。 */
export interface Job {
  id: string;
  project_id: string;
  status:
    | "queued"
    | "running"
    | "succeeded"
    | "answered"
    | "needs_input"
    | "failed"
    | "cancelled"
    | "interrupted";
  result_version_id: string | null;
  questions: string[];
  message: string | null;
}
/** 公开历史消息；提交中的图片暂用 File，持久消息使用服务端素材 ID。 */
/** 每条消息最多附带的参考图数量，与服务端契约一致。 */
export const MAX_MESSAGE_IMAGES = 4;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  images?: File[];
  sequence?: number;
  job_id?: string;
  image_asset_ids?: string[];
  created_at?: string;
}

/**
 * 预览用的示例关键词。有以 keywordEffect 开头的顶层参数（关键词 Preset 的开关或关键词外观，与服务端
 * `categories.has_keyword_effect` 一致）说明这个字效在生成时带了关键词字效；默认关键词为空时它不出现，所以取当前文字
 * 首个非空行开头至多两个字作示例（文字未改时即默认文字，与生成校验挂载的示例相同；改了文字仍能命中）。
 * 已填关键词、没有关键词字效、关键词字效开关（keywordEffect）已关或没有文字时返回空串；只影响预览，不改参数、不进入保存。
 */
export function sampleKeyword(version: Version | null, values: Values): string {
  const properties = version?.candidate.config_schema.properties ?? {};
  const effect = Object.keys(properties).some((key) => key.startsWith("keywordEffect"));
  if (!effect || values.keywordEffect === false || !Array.isArray(values.keywords) || values.keywords.length || typeof values.text !== "string") return "";
  return values.text.split("\n").find((line) => line.trim())?.match(/\S{1,2}/u)?.[0] ?? "";
}

/** 稳定比较完整参数快照，避免依赖对象属性插入顺序。 */
export function sameValues(left: Values, right: Values): boolean {
  return sameJson(left, right);
}

/** JSON 对象忽略键顺序，数组保留顺序；不把相同对象副本误判为未保存修改。 */
export function sameJson(left: JsonValue, right: JsonValue): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameJson(value, right[index]));
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.prototype.hasOwnProperty.call(right, key) && sameJson(left[key], right[key]));
}

/** 由服务端字段语义生成中文标签；保留未知字段的 schema 标题。 */
export function controlLabel(control: Control): string {
  const path = control["x-imv-target"] ?? "";
  const leaf = path.split("/").at(-1) ?? "";
  const names: Record<string, string> = {
    text: "文字",
    font_family: "字体",
    font_size: "字号",
    font_weight: "字重",
    color: "颜色",
    x: "横向位置",
    y: "纵向位置",
    width: "宽度",
    align: "对齐",
    rotation: "旋转",
    letter_spacing: "字距",
    line_height: "行高",
    blur: "模糊",
  };
  const group = path.includes("/shadows/")
    ? "阴影 · "
    : path.includes("/strokes/")
      ? "描边 · "
      : "";
  return group + (control.title || names[leaf] || leaf);
}

/** 数值控件遵循 schema 范围，通用布局和样式使用当前服务端模型的边界。 */
export function numericRules(control: Control): {
  min?: number;
  max?: number;
  step: number;
  percent: boolean;
} {
  const path = control["x-imv-target"] ?? "";
  const leaf = path.split("/").at(-1) ?? "";
  const percent = /\/layout\/(x|y|width)$/.test(path);
  let range: [number, number] | undefined;
  if (percent) range = [leaf === "width" ? 0.001 : 0, 1];
  else if (path.includes("/shadows/"))
    range = leaf === "blur" ? [0, 100] : [-100, 100];
  else if (path.includes("/strokes/") && leaf === "width") range = [0, 50];
  else
    range = (
      {
        font_size: [0.1, 600],
        rotation: [-360, 360],
        letter_spacing: [-20, 100],
        line_height: [0.5, 3],
      } as Record<string, [number, number]>
    )[leaf];
  return {
    min: control.minimum ?? range?.[0],
    max: control.maximum ?? range?.[1],
    step: control.type === "integer" ? 1 : percent ? 0.001 : 0.1,
    percent,
  };
}

/** 校验当前支持的扁平参数；非法草稿不会进入预览或提交队列。 */
export function validValue(control: Control, value: JsonValue): boolean {
  if (control.type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (control.type === "array") return Array.isArray(value);
  if (control.type === "null") return value === null;
  if (control.enum && !control.enum.some(item => sameJson(item, value))) return false;
  if (control.type === "string") {
    if (typeof value !== "string") return false;
    if (
      (control["x-imv-target"] ?? "").endsWith("/color") &&
      !/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value)
    )
      return false;
    const max =
      control.maxLength ??
      ((control["x-imv-target"] ?? "").endsWith("/text") ? 2000 : undefined);
    return (
      value.length >= (control.minLength ?? 0) &&
      (max === undefined || value.length <= max) &&
      (!(control["x-imv-target"] ?? "").endsWith("/text") || !!value.trim())
    );
  }
  if (control.type === "boolean") return typeof value === "boolean";
  const { min, max } = numericRules(control);
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (control.type !== "integer" || Number.isInteger(value)) &&
    (min === undefined || value >= min) &&
    (max === undefined || value <= max)
  );
}

/** 背景只接受 HTTP(S) 直链；不允许凭据、脚本或本地文件协议。 */
export function backgroundUrl(value: string): string {
  if (!value.trim()) return "";
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("请输入有效的视频直链");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error("视频直链仅支持不含账号密码的 HTTP(S) 地址");
  return url.href;
}

/** 会话快照和 SSE 公开任务附带稳定时间与待验收参数。 */
export interface SessionJob extends Job {
  created_at: string;
  updated_at: string;
  parameters: Values | null;
  progress?: ProgressStep[];
  rounds?: LoopRound[];
}
/**
 * 一次宿主实际执行的工具调用：工具名、结论与模型给出的参数。
 * `arguments` 是模型发送的原始 JSON 文本（服务端截断），`detail` 是工具的真实错误说明；旧记录没有这两项。
 */
export interface RoundCall {
  tool: string;
  /** 工具名未知时模型实际请求的名称。 */
  requested_tool?: string | null;
  status: "pass" | "fail";
  error_code?: string | null;
  message?: string | null;
  detail?: string | null;
  arguments?: string | null;
}
/** 一层 ReAct 的一轮；没有 calls 的轮次带模型回复或失败原因（旧记录两者都没有）。 */
export interface LoopRound {
  layer: "outer" | "plan" | "executor";
  turn: number;
  calls: RoundCall[];
  error_code?: "MODEL_CONTRACT_FAILED" | "MODEL_FAILED" | "CANCELLED" | null;
  /** 没有调用工具时模型本轮的文字回复（截断）。 */
  message?: string | null;
  /** 模型请求失败或协议无效时的原因文本。 */
  error_detail?: string | null;
}
/** 宿主公开的阶段摘要；只含白名单阶段与时间，不含候选代码或内部评审。 */
export interface ProgressStep {
  phase:
    | "understanding"
    | "target_review"
    | "answering"
    | "generating"
    | "rendering"
    | "reviewing"
    | "sampling"
    | "adjusting"
    | "adjusting_layout"
    | "adjusting_style"
    | "adjusting_text"
    | "preparing";
  started_at: string;
  ended_at: string | null;
  status: "active" | "done" | "stopped";
}
/** 历史列表只加载标题、活动时间和当前任务，不加载代码；`deleting` 表示服务端已报告删除或正在清理。 */
export interface WorkSummary {
  deleting?: boolean;
  id: string;
  title: string;
  updated_at: string;
  current_version_id: string | null;
  job: SessionJob;
}
/** 单一读取快照提供消息分页、成功版本指针及后续订阅起点。 */
export interface SessionSnapshot {
  work: { id: string; current_version_id: string | null };
  job: SessionJob;
  messages: ChatMessage[];
  next_before: number | null;
  cursor: number;
  jobs?: SessionJob[];
}
/** 事件载荷按公开类型区分；内部候选、steer 与工具轨迹不进入客户端。 */
export type WorkEvent = { id: number; work_id: string; created_at: string } & (
  | { type: "message.created"; data: ChatMessage }
  | { type: "job.updated"; data: SessionJob }
  | { type: "job.round"; data: { job_id: string; round: LoopRound } }
  | { type: "version.ready"; data: { version_id: string; job_id: string } }
);

/** 任务状态使用用户可操作的说明，不暴露内部版本验收阶段。 */
export function jobLabel(status: Job["status"]): string {
  return {
    queued: "排队中",
    running: "正在处理",
    succeeded: "已完成",
    answered: "已回答",
    needs_input: "等待补充",
    failed: "待重试",
    cancelled: "已停止",
    interrupted: "待恢复",
  }[status];
}
