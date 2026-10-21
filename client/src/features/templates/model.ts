/** 模板配置和初始草稿；对象保存时间规则，预览媒体由工作区独立持有。 */
import type { ParameterValue } from "@/components/ParameterControl";
import type { ClipTransform } from "@/features/sprites/parameters";

/** IMS Transition 与 DLTransition 的默认持续时间，单位为秒。 */
export const defaultTransitionDuration = 1;

/** 新建模板的示例配置；与服务端 EffectTemplateEditor 默认值保持一致。 */
export const defaultEditor = {
  title: "让每一帧 都有风格",
  subtitle: "选择花字、滤镜和特效，看看组合效果",
  bubbleText: "超值特惠",
  titleFont: "Alibaba PuHuiTi",
  subtitleFont: "Alibaba PuHuiTi",
  titleSize: 40,
  subtitleSize: 26,
  bubbleSize: 32,
  titleX: 50,
  titleY: 8,
  subtitleX: 50,
  subtitleY: 82,
  bubbleX: 25,
  bubbleY: 32,
  titleFlower: "",
  subtitleFlower: "",
  bubble: "",
  filter: "",
  vfx: "",
  transition: "",
  titleIn: "",
  titleOut: "",
  titleLoop: "",
  subtitleIn: "",
  subtitleOut: "",
  subtitleLoop: "",
  bubbleIn: "",
  bubbleOut: "",
  bubbleLoop: "",
  titleInDuration: 0.5,
  titleOutDuration: 0.5,
  subtitleInDuration: 0.5,
  subtitleOutDuration: 0.5,
  bubbleInDuration: 0.5,
  bubbleOutDuration: 0.5,
  titleKeyword: "",
  titleKeywordBold: false,
  titleKeywordItalic: false,
  titleKeywordUnderline: false,
  titleKeywordStrikeout: false,
  titleKeywordColor: "",
  titleKeywordSize: 0,
  subtitleKeywordBold: false,
  subtitleKeywordItalic: false,
  subtitleKeywordUnderline: false,
  subtitleKeywordStrikeout: false,
  subtitleKeywordColor: "",
  subtitleKeywordSize: 0,
};

/** 三类文字共享字号、位置和动画设置。 */
export const textRoles = {
  title: "顶部标题",
  subtitle: "底部字幕",
  bubble: "气泡字",
} as const;
export type TextRole = keyof typeof textRoles;
export type TextLanguage = "zh" | "en";
export type Editor = typeof defaultEditor;
export type Category =
  | "flower"
  | "bubble"
  | "filter"
  | "vfx/normal"
  | "transition/normal"
  | "in"
  | "out"
  | "loop";

/** 每个效果控件只允许选择对应目录，保存时去重生成 effect_ids。 */
export const effectGroups = {
  titleFlower: "flower",
  subtitleFlower: "flower",
  bubble: "bubble",
  filter: "filter",
  vfx: "vfx/normal",
  transition: "transition/normal",
  titleIn: "in",
  titleOut: "out",
  titleLoop: "loop",
  subtitleIn: "in",
  subtitleOut: "out",
  subtitleLoop: "loop",
  bubbleIn: "in",
  bubbleOut: "out",
  bubbleLoop: "loop",
} as const satisfies Partial<Record<keyof Editor, Category>>;
export type EffectKey = keyof typeof effectGroups;

/** SDK 效果目录项；parameters 来自 SDK 或随版本固定的动画目录。 */
export interface EffectAsset {
  id: string;
  category: Category;
  name: string;
  effect_id: string;
  parameters: Record<string, string>;
  preview_url: string;
}

/** 模板里的一次 Remotion 片段摆放：引用已发布的 Sprite，起点为秒；列表先后决定叠放层级，先者在下。 */
export interface RemotionClip {
  /** 旧合成用途的读写兼容；新模板使用 text_target 与资产分类，不再提供独立用途控件。 */
  business_target?: "title" | "subtitle" | "decoration";
  /** 模板内唯一的摆放 ID，同一个 Sprite 可以摆放多次。 */
  id: string;
  sprite_id: string;
  start: number;
  /** 秒数；null 表示沿用 Sprite 自身的时长。 */
  duration: number | null;
  /** 这次摆放修改过的参数（key 为发布参数名）；未列出的参数沿用资产默认值。 */
  parameters: Record<string, ParameterValue>;
  /** 宿主变换：内容中心位置、缩放、旋转与不透明度；未设置的字段取默认值。 */
  transform: ClipTransform;
  /** 字效作用的文字对象：该文字改由这个片段显示，起点与时长跟随它。 */
  text_target?: "title" | "subtitle";
  language?: TextLanguage;
  /** 转场候选序号，IMS 与 Remotion 共用，预览按选择方式取两个效果。 */
  transition_boundary?: number;
}

/** 用户可修改的完整配置，不包含服务端标识和时间。 */
export interface Draft {
  name: string;
  description: string;
  transition_duration_seconds: number;
  transition_mode?: "sequence" | "random";
  transition_enabled?: boolean;
  tracks: EffectTrack[];
  /** Remotion 片段不进入 IMS 效果与 SDK 预览，按分类占用文字对象、视频交界处或特效名额，随模板一并保存。 */
  clips: RemotionClip[];
}

/** 单个对象的参数面板输入，只在编辑期间使用，不作为模板保存。 */
export interface EffectDraft {
  editor: Editor;
  transition_duration_seconds: number;
}

/** 预览媒体信息由浏览器读取，用于本次时间计算、画布分辨率和画面比例。 */
export interface MasterVideo {
  url: string;
  duration: number;
  width: number;
  height: number;
}

/** 三个预览来源按视频编号保存，排列由工作区独立管理。 */
export type PreviewMedia = readonly [MasterVideo, MasterVideo, MasterVideo];

/** 每个特效实例拥有独立时间和参数；文字动画保存在所属文字实例内。 */
export interface EffectTrack {
  id: string;
  target: TextRole | "filter" | "vfx" | "transition";
  start_mode?: "seconds" | "percent";
  start?: number;
  duration: number | null;
  editor: Editor;
  /** 省略为中文；英文关闭后保留配置，预览与合成时跳过。 */
  language?: TextLanguage;
  enabled?: boolean;
  /** 转场列表序号，且不保存 start/start_mode；其他对象省略此字段。 */
  transition_boundary?: number;
}

/** 文字槽位由用途和语言共同确定，旧模板省略语言仍是中文。 */
export function textKey(target: string, language: TextLanguage = "zh"): string {
  return language === "zh" ? target : `${target}:en`;
}

/** 按用途和语言查找字效所属的文字对象。 */
export function boundText(tracks: EffectTrack[], clip: RemotionClip): EffectTrack | undefined {
  return clip.text_target ? tracks.find((track) => textKey(track.target, track.language) === textKey(clip.text_target!, clip.language)) : undefined;
}

/** 英文初始示例与位置；字号、位置等仍保存为普通编辑参数。 */
export function englishEditor(editor: Editor, target: "title" | "subtitle"): Editor {
  return { ...editor, [target]: target === "title" ? "Make every frame count" : "Give your story a new style",
    titleKeyword: "", [`${target}Size`]: Math.max(12, Math.round(editor[`${target}Size`] * 0.8)),
    [`${target}Y`]: Math.min(95, editor[`${target}Y`] + 6) };
}

/** 仅供展示使用；保存始终使用完整草稿，停用的英文及其资产绑定也保留。 */
export function activeDraft(draft: Draft): Draft {
  return {
    ...draft,
    tracks: draft.tracks.filter((track) => track.enabled !== false),
    clips: draft.clips.filter((clip) => clip.language !== "en" || boundText(draft.tracks, clip)?.enabled !== false),
  };
}

/** 列表只携带展示信息与校验结果，无效记录可以定位和删除。 */
export interface TemplateSummary {
  template_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  title: string;
  subtitle: string;
  track_count?: number;
  validation_error: string;
}

/** 后端返回的完整模板，effects 为保存时的可信快照。 */
export interface Template extends Draft {
  template_id: string;
  effect_ids: string[];
  effects: EffectAsset[];
  created_at: string;
  updated_at: string;
}

/** 每次创建独立草稿，避免新建和已保存模板共享可变引用。 */
export function newDraft(): Draft {
  return {
    name: "",
    description: "",
    tracks: (["title", "subtitle"] as const).map((target) => ({
      id: target, target, start_mode: "seconds", start: 0, duration: null,
      editor: { ...defaultEditor, title: "", subtitle: "", bubbleText: "", [target]: defaultEditor[target] },
    })),
    clips: [],
    transition_duration_seconds: defaultTransitionDuration,
    transition_mode: "sequence",
    transition_enabled: true,
  };
}

/** 剥离只读字段；用于编辑与脏状态比较。 */
export function toDraft(template: Template): Draft {
  if ("editor" in template || !Array.isArray(template.tracks))
    throw new Error("模板格式不支持，请重新创建模板");
  return {
    name: template.name,
    description: template.description,
    transition_duration_seconds: template.transition_duration_seconds,
    transition_mode: template.transition_mode ?? "sequence",
    transition_enabled: template.transition_enabled ?? true,
    tracks: structuredClone(template.tracks).map((track) => track.target === "transition" ? { ...track, duration: template.transition_duration_seconds } : track),
    clips: structuredClone(template.clips),
  };
}

/** 提取所选目录 ID，多个文字角色使用同一效果时仅保存一次。 */
export function selectedEffects(editor: Editor): string[] {
  return [
    ...new Set(
      (Object.keys(effectGroups) as EffectKey[])
        .map((key) => editor[key])
        .filter(Boolean),
    ),
  ];
}

/** 从全部对象收集去重后的效果目录 ID。 */
export function draftEffects(draft: Draft): string[] {
  return [...new Set(draft.tracks.flatMap((track) => selectedEffects(track.editor)))];
}
