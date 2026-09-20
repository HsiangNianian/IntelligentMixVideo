/** 模板配置和初始草稿；对象保存时间规则，预览媒体由工作区独立持有。 */

/** IMS Transition 与 DLTransition 的默认持续时间，单位为秒。 */
export const defaultTransitionDuration = 1;

/** 新建模板的示例配置；与服务端 EffectTemplateEditor 默认值保持一致。 */
export const defaultEditor = {
  title: "让每一帧 都有风格",
  subtitle: "选择花字、滤镜和特效，看看组合效果",
  bubbleText: "超值特惠",
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
};

/** 三类文字共享字号、位置和动画设置。 */
export const textRoles = {
  title: "顶部标题",
  subtitle: "底部字幕",
  bubble: "气泡字",
} as const;
export type TextRole = keyof typeof textRoles;
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

/** 用户可修改的完整配置，不包含服务端标识和时间。 */
export interface Draft {
  name: string;
  description: string;
  editor: Editor;
  transition_duration_seconds: number;
  tracks?: EffectTrack[];
}

/** 预览媒体信息由浏览器读取，只用于本次时间计算和画面比例。 */
export interface MasterVideo {
  url: string;
  duration: number;
  width: number;
  height: number;
}

/** 每个特效实例拥有独立时间和参数；文字动画保存在所属文字实例内。 */
export interface EffectTrack {
  id: string;
  target: TextRole | "filter" | "vfx" | "transition";
  start_mode: "seconds" | "percent";
  start: number;
  duration: number | null;
  editor: Editor;
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
    editor: { ...defaultEditor },
    transition_duration_seconds: defaultTransitionDuration,
  };
}

/** 剥离只读字段；用于编辑与脏状态比较。 */
export function toDraft(template: Template): Draft {
  return {
    name: template.name,
    description: template.description,
    editor: { ...template.editor },
    transition_duration_seconds: template.transition_duration_seconds,
    ...(template.tracks ? { tracks: structuredClone(template.tracks) } : {}),
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

/** 多轨模板从全部实例收集目录 ID；历史模板沿用原有字段。 */
export function draftEffects(draft: Draft): string[] {
  return [...new Set(draft.tracks ? draft.tracks.flatMap((track) => selectedEffects(track.editor)) : selectedEffects(draft.editor))];
}
