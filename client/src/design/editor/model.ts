/**
 * 编辑页原型的数据模型与假数据：画面对象（标题、字幕、气泡、画面特效、滤镜）、三段预览视频、转场设置、
 * IMS 特效目录（八类，数量与正式目录一致）、AI 资产，以及对象在时间轴上的实际区间计算。
 * 字段含义贴近 features/templates 的模板草稿，接入时替换为真实的 Draft 与 resolveTemplate 结果。
 */
import type { CSSProperties } from "react";
import type { IconName } from "../icons";

export type TextKind = "title" | "subtitle" | "bubble";
export type ObjectKind = TextKind | "vfx" | "filter";

/** 开始方式（秒数或视频时长百分比）与持续时间（null 表示持续到结尾）。 */
export interface Timing {
  startMode: "seconds" | "percent";
  start: number;
  duration: number | null;
}

/** 关键词样式；color、size 为 null 表示沿用文字本身的颜色与字号。 */
export interface Keyword {
  text: string;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  color: string | null;
  size: number | null;
}

export interface TextObject {
  kind: TextKind;
  text: string;
  effect: string | null;
  font: string;
  size: number;
  x: number;
  y: number;
  enter: string | null;
  enterDuration: number;
  exit: string | null;
  exitDuration: number;
  loop: string | null;
  timing: Timing;
  keyword: Keyword;
}

export interface EffectObject {
  kind: "vfx" | "filter";
  effect: string | null;
  timing: Timing;
}

export type EditorObject = TextObject | EffectObject;

export interface Transitions {
  enabled: boolean;
  mode: "sequence" | "random";
  duration: number;
  candidates: string[];
}

/** 画布与三段预览视频（tone 对应插图画面色调）。 */
export const canvas = { width: 1080, height: 1920 };
export const videos = [
  { label: "视频 1", duration: 5.2, tone: 0 },
  { label: "视频 2", duration: 4.8, tone: 1 },
  { label: "视频 3", duration: 5.1, tone: 2 },
];
export const totalDuration = videos.reduce((sum, video) => sum + video.duration, 0);

/** 视频连接处的时刻（秒），转场围绕它居中。 */
export const joins = videos.slice(0, -1).map((_, index) => videos.slice(0, index + 1).reduce((sum, video) => sum + video.duration, 0));

/** 预览画面的三种色调：与主页插图同一族的深色画面，带一点暖色或冷色区分三段素材。 */
export const footage = [
  "radial-gradient(80% 50% at 30% 18%, oklch(0.72 0.12 60 / 0.55), transparent 65%), radial-gradient(70% 50% at 80% 90%, oklch(0.5 0.1 20 / 0.5), transparent 70%), linear-gradient(170deg, oklch(0.46 0.07 50), oklch(0.2 0.04 30))",
  "radial-gradient(80% 50% at 70% 20%, oklch(0.75 0.1 200 / 0.5), transparent 65%), radial-gradient(70% 50% at 20% 85%, oklch(0.45 0.09 170 / 0.5), transparent 70%), linear-gradient(170deg, oklch(0.44 0.07 190), oklch(0.19 0.04 220))",
  "radial-gradient(80% 50% at 35% 15%, oklch(0.74 0.1 300 / 0.5), transparent 65%), radial-gradient(70% 50% at 75% 88%, oklch(0.48 0.12 330 / 0.45), transparent 70%), linear-gradient(170deg, oklch(0.42 0.08 285), oklch(0.18 0.04 270))",
];

/** 每类对象的显示名、图标与时间轴轨道。 */
export const objectMeta: Record<ObjectKind, { label: string; icon: IconName; hint: string }> = {
  title: { label: "标题", icon: "text-field", hint: "顶部标题" },
  subtitle: { label: "字幕", icon: "subtitles", hint: "底部字幕，按切片显示" },
  bubble: { label: "气泡", icon: "chat-round-line", hint: "气泡文字" },
  vfx: { label: "画面特效", icon: "magic-wand", hint: "最多一个" },
  filter: { label: "滤镜", icon: "filters", hint: "可叠加多个" },
};

const noKeyword: Keyword = { text: "", bold: false, italic: false, underline: false, strike: false, color: null, size: null };

/** 打开「带货口播 · 黄色花字」时的草稿。 */
export const initialObjects: Record<ObjectKind, EditorObject> = {
  title: {
    kind: "title", text: "好物推荐 现在就出发", effect: "CS0003-000010", font: "阿里巴巴普惠体", size: 76, x: 50, y: 13,
    enter: "弹跳入场", enterDuration: 0.5, exit: null, exitDuration: 0.5, loop: null,
    timing: { startMode: "seconds", start: 0, duration: null },
    keyword: { ...noKeyword, text: "好物", bold: true, color: "#FFE14D" },
  },
  subtitle: {
    kind: "subtitle", text: "选择花字、滤镜和特效，看看组合效果", effect: null, font: "思源黑体", size: 40, x: 50, y: 86,
    enter: null, enterDuration: 0.5, exit: null, exitDuration: 0.5, loop: null,
    timing: { startMode: "seconds", start: 0, duration: null },
    keyword: { ...noKeyword, bold: true },
  },
  bubble: {
    kind: "bubble", text: "限时优惠", effect: "CS0005-000003", font: "阿里巴巴普惠体", size: 44, x: 72, y: 40,
    enter: "放大", enterDuration: 0.4, exit: "渐隐", exitDuration: 0.4, loop: "呼吸",
    timing: { startMode: "seconds", start: 2, duration: 3.5 },
    keyword: noKeyword,
  },
  vfx: { kind: "vfx", effect: "星光闪烁", timing: { startMode: "seconds", start: 6.5, duration: 4 } },
  filter: { kind: "filter", effect: "暖阳", timing: { startMode: "seconds", start: 0, duration: null } },
};

export const initialTransitions: Transitions = { enabled: true, mode: "sequence", duration: 1, candidates: ["渐隐", "向左擦除"] };

/** 按开始方式与持续时间算出对象在预览里的实际区间（秒），越过结尾的部分截短。 */
export function spanOf(timing: Timing): [number, number] {
  const start = timing.startMode === "percent" ? (totalDuration * timing.start) / 100 : timing.start;
  const end = timing.duration === null ? totalDuration : Math.min(totalDuration, start + timing.duration);
  return [Math.min(start, totalDuration), end];
}

/** 每个连接处实际使用的转场：顺序方式按列表循环，随机方式在原型里固定取一个打乱后的结果。 */
export function transitionAt(transitions: Transitions, index: number): string | null {
  const { enabled, mode, candidates } = transitions;
  if (!enabled || candidates.length === 0) return null;
  if (mode === "sequence") return candidates[index % candidates.length];
  const shuffled = [...candidates].reverse();
  return shuffled[index] ?? null;
}

/** 可选字体（与模板字体目录一致的十一项中的常用几项）。 */
export const fonts = ["阿里巴巴普惠体", "思源黑体", "思源宋体", "方正黑体", "方正楷体", "站酷快乐体", "微软雅黑", "楷体"];

/** 花字的几种写法：颜色、描边与发光，按编号循环使用，预览画面与素材缩略图共用。 */
export function flowerStyle(code: string | null): CSSProperties {
  const index = code ? Number(code.slice(-2)) % 8 : -1;
  const stroke = (color: string, width: number) =>
    [[1, 0], [-1, 0], [0, 1], [0, -1], [0.7, 0.7], [-0.7, 0.7], [0.7, -0.7], [-0.7, -0.7]].map(([x, y]) => `${x * width}px ${y * width}px 0 ${color}`).join(", ");
  switch (index) {
    case 0: return { color: "#fff", textShadow: `${stroke("#e0245e", 1.6)}, 0 3px 0 #8a0f36`, fontWeight: 900 };
    case 1: return { color: "#fff", textShadow: "0 0 8px #49c6ff, 0 0 18px #2a7fff", fontWeight: 800 };
    case 2: return { color: "#ffe14d", textShadow: `${stroke("#1d1400", 1.6)}, 0 3px 0 #1d1400`, fontWeight: 900 };
    case 3: return { color: "#fff", textShadow: `${stroke("#1a1a1a", 1.4)}`, fontWeight: 800 };
    case 4: return { backgroundImage: "linear-gradient(180deg,#fff6c9,#f2b33d)", WebkitBackgroundClip: "text", color: "transparent", filter: "drop-shadow(0 2px 0 #6b3b00)", fontWeight: 900 };
    case 5: return { color: "#ff9ad5", textShadow: `${stroke("#fff", 1.4)}, 0 0 12px #ff4fb0`, fontWeight: 900 };
    case 6: return { color: "#c7fff4", textShadow: "0 0 6px #19e6c1, 0 0 16px #10b39a", fontWeight: 800 };
    case 7: return { color: "#f6e7c8", textShadow: "0 2px 10px rgb(0 0 0 / 60%)", fontFamily: "var(--font-serif)", fontWeight: 700, letterSpacing: "0.08em" };
    default: return { color: "#fff", textShadow: `${stroke("#000", 1.2)}`, fontWeight: 700 };
  }
}

export type CategoryId = "flower" | "bubble" | "enter" | "exit" | "loop" | "filter" | "vfx" | "transition";

export interface EffectItem {
  id: string;
  name: string;
  code: string;
}

/** 按基础名称循环生成指定数量的目录项，超出部分加序号。 */
function named(base: string[], count: number, prefix: string): EffectItem[] {
  return Array.from({ length: count }, (_, index) => {
    const round = Math.floor(index / base.length);
    const name = `${base[index % base.length]}${round ? ` ${round + 1}` : ""}`;
    return { id: name, name, code: `${prefix}-${String(index + 1).padStart(6, "0")}` };
  });
}

/** 花字、气泡没有中文名，用「花字 06」这样的序号名，编号放在下方。 */
function numbered(label: string, count: number, prefix: string): EffectItem[] {
  return Array.from({ length: count }, (_, index) => {
    const code = `${prefix}-${String(index + 1).padStart(6, "0")}`;
    return { id: code, name: `${label} ${String(index + 1).padStart(2, "0")}`, code };
  });
}

/** IMS 特效目录：分「文字」「画面」两组；文字类需要选择作用对象。数量与正式目录一致。 */
export const categories: { id: CategoryId; label: string; group: "文字" | "画面"; items: EffectItem[]; targets?: TextKind[] }[] = [
  { id: "flower", label: "花字", group: "文字", items: numbered("花字", 75, "CS0003"), targets: ["title", "subtitle"] },
  { id: "bubble", label: "气泡", group: "文字", items: numbered("气泡", 33, "CS0005") },
  { id: "enter", label: "入场", group: "文字", items: named(["渐显", "弹跳入场", "打字机", "放大", "向上飞入", "向左滑入", "模糊渐显", "旋转入场", "逐字弹出"], 36, "TA0001"), targets: ["title", "subtitle", "bubble"] },
  { id: "exit", label: "出场", group: "文字", items: named(["渐隐", "缩小", "向下飞出", "向右滑出", "模糊消失", "逐字消失", "旋转出场"], 34, "TA0002"), targets: ["title", "subtitle", "bubble"] },
  { id: "loop", label: "循环", group: "文字", items: named(["呼吸", "摇摆", "闪烁", "跳动", "波浪", "霓虹", "抖动"], 21, "TA0003"), targets: ["title", "subtitle", "bubble"] },
  { id: "filter", label: "滤镜", group: "画面", items: named(["暖阳", "清晨", "胶片", "复古", "黑白", "冷调", "森系", "日系", "港风", "赛博", "晚霞", "奶油", "青橙", "蓝调"], 70, "FL0001") },
  { id: "vfx", label: "特效", group: "画面", items: named(["星光闪烁", "光斑", "粒子飘落", "闪光灯", "漏光", "雪花", "烟花", "爱心", "彩带", "光晕", "故障", "放射线", "聚光灯"], 106, "VF0001") },
  { id: "transition", label: "转场", group: "画面", items: named(["渐隐", "向左擦除", "向右擦除", "向上推入", "向下推入", "溶解", "缩放", "旋转", "百叶窗", "闪白", "闪黑", "模糊过渡", "圆形展开"], 63, "TR0001") },
];

/** AI 资产（Remotion 发布的字效、滤镜、视频特效与转场）。 */
export const sprites = [
  { id: "sp-1", name: "金色电影标题", kind: "字效", tone: 0 },
  { id: "sp-2", name: "霓虹描边字幕", kind: "字效", tone: 2 },
  { id: "sp-3", name: "逐字弹跳标题", kind: "字效", tone: 1 },
  { id: "sp-4", name: "胶片颗粒", kind: "滤镜", tone: 0 },
  { id: "sp-5", name: "星尘粒子", kind: "视频特效", tone: 2 },
  { id: "sp-6", name: "光斑扫过", kind: "转场", tone: 1 },
];

/** 秒数写成「00:05.2」；先按十分之一秒取整，避免 15.1 这样的值因浮点误差显示成 15.0。 */
export function formatTime(seconds: number): string {
  const tenths = Math.floor(Math.round(seconds * 100) / 10);
  const whole = Math.floor(tenths / 10);
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}.${tenths % 10}`;
}
