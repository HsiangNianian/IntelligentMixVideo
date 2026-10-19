/**
 * AI 字效工作区原型的数据模型与假数据：会话、消息、成功版本、处理阶段与循环记录、字效参数及其分组定义。
 * 状态与阶段名称沿用 features/remotion_templates 的公开状态与阶段白名单，接入时替换为本地会话库与作品级事件流。
 */
import type { IconName } from "../icons";

/** 任务状态：排队、进行中、已完成、只回答了问题、等待补充（澄清）、失败、已停止。 */
export type JobStatus = "queued" | "running" | "done" | "answered" | "clarify" | "failed" | "stopped";

/** 字效参数：每个成功版本各自带一份，手动修改后保存为新的参数修订版本。 */
export interface Params {
  text: string;
  fontFamily: string;
  fontSize: number;
  fontWeight: "500" | "700" | "900";
  color: string;
  glow: number;
  underline: number;
  enter: number;
  y: number;
}

export interface Version {
  id: string;
  number: number;
  title: string;
  /** agent：智能体生成；params：用户手动调参后保存。 */
  source: "agent" | "params";
  time: string;
  kind: "字效" | "滤镜" | "视频特效" | "转场";
  params: Params;
}

/** 循环记录里的一次工具调用：层级、工具名与参数摘要。 */
export interface Round {
  layer: "外层" | "计划" | "执行";
  tool: string;
  args: string;
  ok: boolean;
}

export interface Stage {
  label: string;
  seconds: number;
  rounds?: Round[];
}

export type Message =
  /** images：示意参考图的序号，或用户刚添加的本地图片地址。 */
  | { id: string; role: "user"; text: string; images: (number | string)[] }
  | { id: string; role: "assistant"; text: string; status: JobStatus; stages: Stage[]; versionId?: string; question?: string; error?: string };

export interface Session {
  id: string;
  title: string;
  status: JobStatus | "deleting";
  group: "今天" | "昨天" | "更早";
  composition: Composition;
  messages: Message[];
  versions: Version[];
}

export interface Composition {
  width: number;
  height: number;
  fps: number;
  seconds: number;
}

export const defaultComposition: Composition = { width: 1080, height: 1920, fps: 30, seconds: 5 };

/** 画布预设。 */
export const canvasPresets = [
  { label: "竖屏", width: 1080, height: 1920 },
  { label: "横屏", width: 1920, height: 1080 },
  { label: "方形", width: 1080, height: 1080 },
];

/** 公开阶段的白名单顺序，生成过程按这个顺序依次出现。 */
export const stagePlan = ["理解你的需求", "确认制作目标", "生成模板代码", "渲染并检查效果", "验收文字与样式", "准备可用结果"];

/** 参考图的示意画面（真实使用时是用户上传的图片）。 */
export const referenceShots = [
  "radial-gradient(70% 60% at 30% 30%, oklch(0.8 0.12 75 / 0.8), transparent 60%), linear-gradient(160deg, oklch(0.45 0.08 60), oklch(0.2 0.04 40))",
  "radial-gradient(70% 60% at 70% 30%, oklch(0.75 0.15 330 / 0.7), transparent 60%), linear-gradient(160deg, oklch(0.4 0.1 300), oklch(0.18 0.05 280))",
];

const v1: Params = { text: "让灵感发光", fontFamily: "思源宋体", fontSize: 96, fontWeight: "700", color: "#F6D58E", glow: 0.25, underline: 2, enter: 1.2, y: 50 };
const v2: Params = { ...v1, text: "每一帧都值得", underline: 4, glow: 0.6 };

const doneStages = (extra = 0): Stage[] => [
  { label: "理解你的需求", seconds: 4 + extra },
  { label: "确认制作目标", seconds: 3 },
  {
    label: "生成模板代码",
    seconds: 21 + extra,
    rounds: [
      { layer: "外层", tool: "tools_plan_execute", args: '{"action":"delegate","goal":"暖金色衬线标题，下划线从中间展开"}', ok: true },
      { layer: "执行", tool: "preset_modify", args: '{"draft":"base_title","props":{"color":"#F6D58E"}}', ok: true },
      { layer: "执行", tool: "sprite_compose", args: '{"kind":"text","parameters":["text","fontSize","color"]}', ok: true },
      { layer: "执行", tool: "sprite_create", args: '{"draft_id":"d_8f2c"}', ok: true },
    ],
  },
  { label: "渲染并检查效果", seconds: 12 },
  { label: "验收文字与样式", seconds: 8 },
  { label: "准备可用结果", seconds: 2 },
];

/** 当前会话的完整对话：两轮生成、一张参考图。 */
const mainSession: Session = {
  id: "s-1",
  title: "温暖的电影感标题",
  status: "done",
  group: "今天",
  composition: defaultComposition,
  versions: [
    { id: "v-1", number: 1, title: "让灵感发光", source: "agent", time: "14:12", kind: "字效", params: v1 },
    { id: "v-2", number: 2, title: "每一帧都值得", source: "agent", time: "14:26", kind: "字效", params: v2 },
  ],
  messages: [
    { id: "m-1", role: "user", text: "做一个温暖的电影感标题，文字是「让灵感发光」，暖金色衬线字，下面一条细下划线从中间展开，整体慢慢淡入。", images: [0] },
    { id: "m-2", role: "assistant", text: "做好了 V1：暖金色衬线标题，1.2 秒淡入，下划线在文字出现后从中间向两侧展开。字号、颜色、光晕和下划线粗细都可以在右侧直接调整。", status: "done", stages: doneStages(), versionId: "v-1" },
    { id: "m-3", role: "user", text: "文字改成「每一帧都值得」，下划线再粗一点，加一圈柔和的光晕。", images: [] },
    { id: "m-4", role: "assistant", text: "已更新为 V2：换了文字，下划线加粗到 4，并加了一层与文字同色的柔光。", status: "done", stages: doneStages(-2), versionId: "v-2" },
  ],
};

/** 其他会话只放一轮对话，用来展示进行中、等待补充、失败等状态。 */
export const initialSessions: Session[] = [
  mainSession,
  {
    id: "s-2", title: "霓虹描边字幕", status: "running", group: "今天", composition: defaultComposition, versions: [],
    messages: [
      { id: "n-1", role: "user", text: "做一个霓虹灯描边的字幕，粉紫色，闪两下再亮起来。", images: [1] },
      { id: "n-2", role: "assistant", text: "", status: "running", stages: [{ label: "理解你的需求", seconds: 5 }, { label: "确认制作目标", seconds: 3 }, { label: "生成模板代码", seconds: 0 }] },
    ],
  },
  {
    id: "s-3", title: "逐字弹跳的综艺标题", status: "done", group: "昨天", composition: defaultComposition,
    versions: [{ id: "v-3", number: 1, title: "今天吃什么", source: "agent", time: "18:40", kind: "字效", params: { ...v1, text: "今天吃什么", fontFamily: "站酷快乐体", color: "#FFFFFF", glow: 0, underline: 0, fontWeight: "900" } }],
    messages: [
      { id: "o-1", role: "user", text: "综艺感的标题，白字粗体，每个字依次弹出来。", images: [] },
      { id: "o-2", role: "assistant", text: "做好了 V1：每个字间隔 0.08 秒依次弹出，落地时轻微回弹。", status: "done", stages: doneStages(), versionId: "v-3" },
    ],
  },
  {
    id: "s-4", title: "手写签名效果", status: "clarify", group: "昨天", composition: defaultComposition, versions: [],
    messages: [
      { id: "c-1", role: "user", text: "做一个手写签名一样一笔一笔写出来的效果。", images: [] },
      { id: "c-2", role: "assistant", text: "", status: "clarify", stages: [{ label: "理解你的需求", seconds: 6 }], question: "签名写的是哪几个字？笔画颜色想要黑色、白色还是金色？" },
    ],
  },
  {
    id: "s-5", title: "金色粒子转场", status: "failed", group: "更早", composition: defaultComposition, versions: [],
    messages: [
      { id: "f-1", role: "user", text: "做一个金色粒子从左到右扫过的转场。", images: [] },
      { id: "f-2", role: "assistant", text: "", status: "failed", stages: [{ label: "理解你的需求", seconds: 4 }, { label: "生成模板代码", seconds: 64 }, { label: "渲染并检查效果", seconds: 30 }], error: "渲染检查连续三次没有通过：粒子遮住了整个画面，视频无法透出。" },
    ],
  },
  { id: "s-6", title: "旧版双语字幕", status: "deleting", group: "更早", composition: defaultComposition, versions: [], messages: [] },
];

/** 新对话的灵感提示。 */
export const suggestions = [
  { title: "电影感片头标题", prompt: "暖金色衬线大标题，慢慢淡入，下面有一条从中间展开的细线。", icon: "text-field" as IconName },
  { title: "综艺花字", prompt: "白字粗体加黑色描边，每个字依次弹出来，落地时轻微回弹。", icon: "stars" as IconName },
  { title: "霓虹字幕", prompt: "粉紫色霓虹灯描边的字幕，先闪两下再稳定亮起。", icon: "magic-wand" as IconName },
  { title: "手写签名", prompt: "像手写签名一样一笔一笔写出来，最后整体发一点光。", icon: "pen-new-round" as IconName },
];

/** 参数面板的分组与控件定义；正式接入时由版本的参数契约（JSON Schema）生成。 */
export type ParamControl =
  | { key: "text"; type: "text"; label: string; max: number }
  | { key: "fontFamily"; type: "font"; label: string }
  | { key: "fontWeight"; type: "segmented"; label: string; options: { value: Params["fontWeight"]; label: string }[] }
  | { key: "fontSize" | "glow" | "underline" | "enter" | "y"; type: "range"; label: string; min: number; max: number; step: number; unit?: string }
  | { key: "color"; type: "color"; label: string };

export const paramGroups: { title: string; controls: ParamControl[] }[] = [
  {
    title: "文字",
    controls: [
      { key: "text", type: "text", label: "文字", max: 100 },
      { key: "fontFamily", type: "font", label: "字体" },
      { key: "fontWeight", type: "segmented", label: "字重", options: [{ value: "500", label: "常规" }, { value: "700", label: "粗体" }, { value: "900", label: "特粗" }] },
      { key: "fontSize", type: "range", label: "字号", min: 24, max: 200, step: 1 },
      { key: "color", type: "color", label: "颜色" },
    ],
  },
  {
    title: "效果",
    controls: [
      { key: "glow", type: "range", label: "光晕", min: 0, max: 1, step: 0.05 },
      { key: "underline", type: "range", label: "下划线", min: 0, max: 12, step: 1, unit: "px" },
    ],
  },
  {
    title: "动画与位置",
    controls: [
      { key: "enter", type: "range", label: "淡入", min: 0.2, max: 3, step: 0.1, unit: "秒" },
      { key: "y", type: "range", label: "纵向", min: 5, max: 95, step: 1, unit: "%" },
    ],
  },
];

/** 字体库（与字体库页面共用的家族名，示意用）。 */
export const fontLibrary = ["思源宋体", "思源黑体", "站酷快乐体", "阿里巴巴普惠体", "霞鹜文楷", "得意黑"];

/** 版本的示意源码：按参数拼出一段 Remotion 组件，用于「查看代码」。 */
export function sourceOf(version: Version): string {
  const p = version.params;
  return `import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

export const defaultProps = {
  text: ${JSON.stringify(p.text)},
  fontFamily: ${JSON.stringify(p.fontFamily)},
  fontSize: ${p.fontSize},
  color: ${JSON.stringify(p.color)},
  glow: ${p.glow},
  underline: ${p.underline},
};

export default function Title(props: typeof defaultProps) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const opacity = interpolate(frame, [0, ${p.enter} * fps], [0, 1], { extrapolateRight: "clamp" });
  const line = interpolate(frame, [0.3 * fps, 1.4 * fps], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ opacity, color: props.color, fontSize: props.fontSize }}>
        {props.text}
        <div style={{ height: props.underline, transform: \`scaleX(\${line})\` }} />
      </div>
    </AbsoluteFill>
  );
}`;
}
