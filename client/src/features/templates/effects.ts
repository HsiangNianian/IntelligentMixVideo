/** 资产分类、编辑对象和效果变更规则；左侧资产与右侧表单共同生成可保存的模板草稿。 */
import { defaultEditor, defaultTransitionDuration, effectGroups, textRoles, type Category, type Draft, type EffectAsset, type EffectKey, type TextRole } from "./model";

/** 编辑对象对应现有模板字段；文字对象共享花字、位置和动画设置。 */
export const effectTargets = { ...textRoles, filter: "视频滤镜", vfx: "画面特效", transition: "镜头转场" } as const;
export type EffectTarget = keyof typeof effectTargets;

/** 资产浏览使用与 SDK 相同的分类。 */
export const assetCategories: Record<Category, string> = {
  flower: "花字", bubble: "气泡", filter: "滤镜", "vfx/normal": "画面特效",
  "transition/normal": "转场", in: "文字入场", out: "文字出场", loop: "文字循环",
};

/** 判断当前对象是否具有文字、位置与动画字段。 */
export function isTextTarget(target: EffectTarget): target is TextRole {
  return target === "title" || target === "subtitle" || target === "bubble";
}

/** 返回对象关联的效果字段，供已添加列表和清除操作共同使用。 */
export function targetEffectKeys(target: EffectTarget): EffectKey[] {
  return isTextTarget(target)
    ? [target === "bubble" ? "bubble" : `${target}Flower`, `${target}In`, `${target}Out`, `${target}Loop`]
    : [target];
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
export function changeEffects(draft: Draft, values: Partial<Record<EffectKey, string>>): Draft {
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
export function applyAsset(draft: Draft, asset: EffectAsset, role: TextRole): { draft: Draft; target: EffectTarget } {
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
export function appliedTargets(draft: Draft): EffectTarget[] {
  return (Object.keys(effectTargets) as EffectTarget[]).filter((target) => {
    if (target === "title" || target === "subtitle")
      return Boolean(draft.editor[target].trim()) || targetEffectKeys(target).some((key) => draft.editor[key]);
    return Boolean(draft.editor[target]);
  });
}

/** 清除当前文字对象的样式与动画，并恢复全部文字参数；其他对象和模板信息保持不变。 */
export function resetTextTarget(draft: Draft, target: TextRole): Draft {
  const next = changeEffects(draft, Object.fromEntries(targetEffectKeys(target).map((key) => [key, ""])));
  const textKey = target === "bubble" ? "bubbleText" : target;
  next.editor[textKey] = defaultEditor[textKey];
  next.editor[`${target}Size`] = defaultEditor[`${target}Size`];
  next.editor[`${target}X`] = defaultEditor[`${target}X`];
  next.editor[`${target}Y`] = defaultEditor[`${target}Y`];
  next.editor[`${target}InDuration`] = defaultEditor[`${target}InDuration`];
  next.editor[`${target}OutDuration`] = defaultEditor[`${target}OutDuration`];
  return next;
}

/** 移除对象及其效果；其他对象的文字、动画与位置保持不变。 */
export function removeTarget(draft: Draft, target: EffectTarget): Draft {
  const next = changeEffects(draft, Object.fromEntries(targetEffectKeys(target).map((key) => [key, ""])));
  if (isTextTarget(target)) {
    next.editor[target === "bubble" ? "bubbleText" : target] = "";
    next.editor[`${target}Size`] = defaultEditor[`${target}Size`];
    next.editor[`${target}X`] = defaultEditor[`${target}X`];
    next.editor[`${target}Y`] = defaultEditor[`${target}Y`];
  }
  if (target === "transition" && (!Number.isFinite(next.transition_duration_seconds) || next.transition_duration_seconds < 0.1 || next.transition_duration_seconds > 3))
    next.transition_duration_seconds = defaultTransitionDuration;
  return next;
}
