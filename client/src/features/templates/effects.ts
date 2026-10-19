/** 资产分类、编辑对象和效果变更规则；左侧资产与右侧表单共同生成可保存的模板草稿。 */
import { defaultEditor, defaultTransitionDuration, effectGroups, englishEditor, isKeywordTarget, keywordEditor, textRole, textRoles, type Category, type EffectDraft, type EffectAsset, type EffectKey, type EffectTrack, type KeywordTarget, type TextRole, type TextLanguage } from "./model";

/** 编辑对象对应现有模板字段；文字对象（含关键词对象）共享花字、位置和动画设置。 */
export const effectTargets = { ...textRoles, title_keyword: "标题关键词", subtitle_keyword: "字幕关键词", filter: "视频滤镜", vfx: "画面特效", transition: "镜头转场" } as const;
export type EffectTarget = keyof typeof effectTargets;

/** 资产浏览使用与 SDK 相同的分类。 */
export const assetCategories: Record<Category, string> = {
  flower: "花字", bubble: "气泡", filter: "滤镜", "vfx/normal": "画面特效",
  "transition/normal": "转场", in: "文字入场", out: "文字出场", loop: "文字循环",
};

/** 判断资产能否作为新的效果选择，已有选择由编辑器保留。 */
export function isSelectableAsset(asset: EffectAsset): boolean {
  return asset.id !== "flower/CS0003-000002";
}

/** 判断当前对象是否具有文字、位置与动画字段；关键词对象的字段属于所属标题或字幕（见 `textRole`）。 */
export function isTextTarget(target: EffectTarget): target is TextRole | KeywordTarget {
  return target === "title" || target === "subtitle" || target === "bubble" || isKeywordTarget(target);
}

/** 返回对象关联的效果字段，供已添加列表和清除操作共同使用。 */
export function targetEffectKeys(target: EffectTarget): EffectKey[] {
  if (!isTextTarget(target)) return [target];
  const role = textRole(target);
  return [role === "bubble" ? "bubble" : `${role}Flower`, `${role}In`, `${role}Out`, `${role}Loop`];
}

/** 对象的主效果：文字为花字或气泡样式，其他对象为所选滤镜、特效或转场。 */
export function trackEffect(track: EffectTrack): string {
  return track.editor[targetEffectKeys(track.target)[0]];
}

/** 为指定资产分类选择持久化字段，花字只允许应用到标题或字幕。 */
export function assetField(category: Category, role: TextRole): EffectKey {
  switch (category) {
    case "flower":
      if (role === "bubble") throw new Error("花字请选择顶部标题或底部字幕");
      return `${role}Flower`;
    case "bubble": return "bubble";
    case "filter": return "filter";
    case "vfx/normal": return "vfx";
    case "transition/normal": return "transition";
    case "in": return `${role}In`;
    case "out": return `${role}Out`;
    case "loop": return `${role}Loop`;
  }
}

/** 修改效果时修复被清除动画的无效时长，保留已经合法的时长。 */
export function changeEffects(draft: EffectDraft, values: Partial<Record<EffectKey, string>>): EffectDraft {
  const editor = { ...draft.editor, ...values };
  for (const field of Object.keys(values) as EffectKey[]) {
    if (values[field] || !(field.endsWith("In") || field.endsWith("Out"))) continue;
    const durationKey = `${field}Duration` as `${TextRole}${"In" | "Out"}Duration`;
    const duration = editor[durationKey];
    if (!Number.isFinite(duration) || duration < 0.1 || duration > 3)
      editor[durationKey] = defaultEditor[durationKey];
  }
  return { ...draft, editor };
}

/** 把目录资产分配到真实字段；动画互斥由调用方展示，函数仍拒绝非法组合。 */
export function applyAsset(draft: EffectDraft, asset: EffectAsset, role: TextRole): { draft: EffectDraft; target: EffectTarget } {
  const category = asset.category;
  const field = assetField(category, role);
  const target = field === "bubble" || field === "filter" || field === "vfx" || field === "transition" ? field : role;
  if (category === "in" || category === "out" || category === "loop") {
    if (category === "loop" ? draft.editor[`${role}In`] || draft.editor[`${role}Out`] : draft.editor[`${role}Loop`])
      throw new Error("循环动画与入场、出场动画互斥，请清除当前动画后选择");
    if (role === "bubble" && !draft.editor.bubble) throw new Error("请先添加气泡样式");
  }
  if (effectGroups[field] !== category) throw new Error("效果与目标分类不一致");
  const next = changeEffects(draft, { [field]: asset.id });
  if (isTextTarget(target)) {
    const textKey = target === "bubble" ? "bubbleText" : target;
    if (!next.editor[textKey].trim()) next.editor[textKey] = defaultEditor[textKey];
  }
  return { draft: next, target };
}

/** 列出可编辑的画面对象及已选择效果，包括尚未加载的历史效果。 */
export function appliedTargets(draft: EffectDraft): Exclude<EffectTarget, KeywordTarget>[] {
  // 关键词对象是独立的轨道，不由编辑字段推断。
  return (Object.keys(effectTargets) as EffectTarget[]).filter((target): target is Exclude<EffectTarget, KeywordTarget> => {
    if (isKeywordTarget(target)) return false;
    if (target === "title" || target === "subtitle")
      return Boolean(draft.editor[target].trim()) || targetEffectKeys(target).some((key) => draft.editor[key]);
    return Boolean(draft.editor[target]);
  });
}

/** 清除当前文字对象的样式与动画，并恢复全部文字参数；关键词对象恢复它自己的初始外观；其他对象和模板信息保持不变。 */
export function resetTextTarget(draft: EffectDraft, object: TextRole | KeywordTarget, language?: TextLanguage): EffectDraft {
  const target = textRole(object);
  const next = changeEffects(draft, Object.fromEntries(targetEffectKeys(target).map((key) => [key, ""])));
  const textKey = target === "bubble" ? "bubbleText" : target;
  next.editor[textKey] = defaultEditor[textKey];
  if (target !== "bubble") next.editor[`${target}Font`] = defaultEditor[`${target}Font`];
  next.editor[`${target}Size`] = defaultEditor[`${target}Size`];
  next.editor[`${target}X`] = defaultEditor[`${target}X`];
  next.editor[`${target}Y`] = defaultEditor[`${target}Y`];
  next.editor[`${target}InDuration`] = defaultEditor[`${target}InDuration`];
  next.editor[`${target}OutDuration`] = defaultEditor[`${target}OutDuration`];
  if (target === "title") {
    next.editor.titleKeyword = "";
    next.editor.titleKeywordBold = false;
    next.editor.titleKeywordItalic = false;
    next.editor.titleKeywordUnderline = false;
    next.editor.titleKeywordStrikeout = false;
    next.editor.titleKeywordColor = "";
    next.editor.titleKeywordSize = 0;
  }
  if (target === "subtitle") {
    next.editor.subtitleKeywordBold = false;
    next.editor.subtitleKeywordItalic = false;
    next.editor.subtitleKeywordUnderline = false;
    next.editor.subtitleKeywordStrikeout = false;
    next.editor.subtitleKeywordColor = "";
    next.editor.subtitleKeywordSize = 0;
  }
  if (language === "en" && target !== "bubble") next.editor = englishEditor(next.editor, target);
  if (isKeywordTarget(object) && target !== "bubble") next.editor = keywordEditor(next.editor, target);
  return next;
}

/** 移除对象及其效果；其他对象的文字、动画与位置保持不变。关键词对象不在这里移除，由所属文字的开关停用。 */
export function removeTarget(draft: EffectDraft, target: Exclude<EffectTarget, KeywordTarget>): EffectDraft {
  const next = changeEffects(draft, Object.fromEntries(targetEffectKeys(target).map((key) => [key, ""])));
  if (isTextTarget(target)) {
    next.editor[target === "bubble" ? "bubbleText" : target] = "";
    if (target !== "bubble") next.editor[`${target}Font`] = defaultEditor[`${target}Font`];
    next.editor[`${target}Size`] = defaultEditor[`${target}Size`];
    next.editor[`${target}X`] = defaultEditor[`${target}X`];
    next.editor[`${target}Y`] = defaultEditor[`${target}Y`];
    if (target === "title") {
      next.editor.titleKeyword = "";
      next.editor.titleKeywordBold = false;
      next.editor.titleKeywordItalic = false;
      next.editor.titleKeywordUnderline = false;
      next.editor.titleKeywordStrikeout = false;
      next.editor.titleKeywordColor = "";
      next.editor.titleKeywordSize = 0;
    }
    if (target === "subtitle") {
      next.editor.subtitleKeywordBold = false;
      next.editor.subtitleKeywordItalic = false;
      next.editor.subtitleKeywordUnderline = false;
      next.editor.subtitleKeywordStrikeout = false;
      next.editor.subtitleKeywordColor = "";
      next.editor.subtitleKeywordSize = 0;
    }
  }
  if (target === "transition" && (!Number.isFinite(next.transition_duration_seconds) || next.transition_duration_seconds < 0.1 || next.transition_duration_seconds > 3))
    next.transition_duration_seconds = defaultTransitionDuration;
  return next;
}
