/** Remotion 服务契约与参数解释；只保存当前会话和已验收模板，不暴露内部候选。 */
/** 第一版参数只支持字符串、有限数值和布尔值。 */
export type Scalar = string | number | boolean;
/** 完整参数快照使用服务端生成的扁平键。 */
export type Values = Record<string, Scalar>;
/** 宿主控制的画布与整帧时长，同时用于生成请求和成功版本展示。 */
export interface Composition {
  width: number;
  height: number;
  fps: number;
  duration_in_frames: number;
}
/** 已验收的扁平标量控件，路径绑定服务端目标规格。 */
export interface Control {
  type: "string" | "number" | "integer" | "boolean";
  title?: string;
  description?: string;
  enum?: Scalar[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  "x-imv-target": string;
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
    name: string;
    composition: Composition;
    text_layers: { id: string; text: string }[];
  };
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
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  image?: File;
  sequence?: number;
  job_id?: string;
  image_asset_id?: string | null;
  created_at?: string;
  reconstructed?: boolean;
}

/** 稳定比较完整参数快照，避免依赖对象属性插入顺序。 */
export function sameValues(left: Values, right: Values): boolean {
  return (
    Object.keys(left).length === Object.keys(right).length &&
    Object.keys(left).every((key) => left[key] === right[key])
  );
}

/** 由服务端字段语义生成中文标签；保留未知字段的 schema 标题。 */
export function controlLabel(control: Control): string {
  const path = control["x-imv-target"];
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
  const path = control["x-imv-target"];
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
export function validValue(control: Control, value: Scalar): boolean {
  if (control.enum && !control.enum.includes(value)) return false;
  if (control.type === "string") {
    if (typeof value !== "string") return false;
    if (
      control["x-imv-target"].endsWith("/color") &&
      !/^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(value)
    )
      return false;
    const max =
      control.maxLength ??
      (control["x-imv-target"].endsWith("/text") ? 2000 : undefined);
    return (
      value.length >= (control.minLength ?? 0) &&
      (max === undefined || value.length <= max) &&
      (!control["x-imv-target"].endsWith("/text") || !!value.trim())
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
/** 历史列表只加载标题、活动时间和当前任务，不加载代码。 */
export interface WorkSummary {
  deleting?: boolean;
  id: string;
  title: string;
  updated_at: string;
  current_version_id: string | null;
  job: SessionJob;
}
/** 最近活动分页使用服务端游标，避免偏移分页重复记录。 */
export interface WorkPage {
  items: WorkSummary[];
  next_cursor: string | null;
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
