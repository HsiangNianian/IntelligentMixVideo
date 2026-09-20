/** 资产编辑规则测试：使用随包真实目录验证字段分配、互斥、移除与草稿隔离；执行 bun run test。 */
import { expect, test } from "bun:test";
import { appliedTargets, applyAsset, assetField, changeEffects, removeTarget, resetTextTarget, targetEffectKeys } from "@/features/templates/effects";
import { defaultEditor, newDraft, selectedEffects, type Category, type TextRole } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";

/** 从随包目录中获取真实资产，缺少所需分类时立即报告测试失败。 */
function asset(category: Category, index = 0) {
  const item = readCatalog().filter((item) => item.category === category)[index];
  if (!item) throw new Error(`目录缺少 ${category} 的第 ${index + 1} 个资产`);
  return item;
}

// 场景：全部非动画分类选入正确字段，返回相应编辑对象，输入草稿保持不变。
test.each([
  ["flower", "title", "titleFlower", "title"],
  ["flower", "subtitle", "subtitleFlower", "subtitle"],
  ["bubble", "title", "bubble", "bubble"],
  ["filter", "subtitle", "filter", "filter"],
  ["vfx/normal", "title", "vfx", "vfx"],
  ["transition/normal", "title", "transition", "transition"],
] as const)("资产 %s 应用到 %s", (category, role, field, target) => {
  const original = newDraft();
  const item = asset(category);
  const next = applyAsset(original, item, role);
  expect(next.target).toBe(target);
  expect(next.draft.editor[field]).toBe(item.id);
  expect(original).toEqual(newDraft());
  expect(selectedEffects(next.draft.editor)).toEqual([item.id]);
  expect(appliedTargets(next.draft)).toContain(target);
});

// 场景：同类替换和重复选择保留当前文字、位置及其他对象，只保存最后选择的资产。
test("同类资产替换保留独立参数，重复选择保持相同草稿", () => {
  const original = newDraft();
  original.editor.title = "自定义标题";
  original.editor.titleX = 24;
  const first = applyAsset(original, asset("flower"), "title").draft;
  const second = applyAsset(first, asset("flower", 1), "title").draft;
  const same = applyAsset(second, asset("flower", 1), "title").draft;
  expect(second.editor.title).toBe("自定义标题");
  expect(second.editor.titleX).toBe(24);
  expect(second.editor.subtitle).toBe(original.editor.subtitle);
  expect(second.editor.titleFlower).not.toBe(first.editor.titleFlower);
  expect(selectedEffects(second.editor)).toEqual([asset("flower", 1).id]);
  expect(same).toEqual(second);
});

// 场景：三个文字对象分别使用真实动画，入场与出场可共存，循环与二者互斥。
test.each(["title", "subtitle", "bubble"] as TextRole[])("%s 动画支持独立配置并拒绝互斥组合", (role) => {
  const draft = role === "bubble" ? applyAsset(newDraft(), asset("bubble"), role).draft : newDraft();
  const entered = applyAsset(draft, asset("in"), role).draft;
  const exited = applyAsset(entered, asset("out"), role).draft;
  expect(exited.editor[`${role}In`]).toBe(asset("in").id);
  expect(exited.editor[`${role}Out`]).toBe(asset("out").id);
  expect(() => applyAsset(exited, asset("loop"), role)).toThrow("互斥");
  const looped = applyAsset(draft, asset("loop"), role).draft;
  expect(() => applyAsset(looped, asset("in"), role)).toThrow("互斥");
  expect(() => applyAsset(looped, asset("out"), role)).toThrow("互斥");
});

// 场景：气泡动画需要已经添加的气泡，花字无法写入气泡字段。
test("错误的文字目标与未添加的气泡被拒绝", () => {
  expect(() => applyAsset(newDraft(), asset("in"), "bubble")).toThrow("请先添加气泡");
  expect(() => assetField("flower", "bubble")).toThrow("花字请选择");
});

// 场景：移除文字清除其效果与非法数值，保留其他对象；再次应用会恢复可见文字。
test("移除文字后可以重新添加，其他对象的配置保持不变", () => {
  const draft = applyAsset(newDraft(), asset("flower"), "title").draft;
  draft.editor.subtitleIn = asset("in").id;
  draft.editor.titleIn = asset("in").id;
  draft.editor.titleInDuration = NaN;
  draft.editor.titleSize = NaN;
  const next = removeTarget(draft, "title");
  expect(appliedTargets(next)).toEqual(["subtitle"]);
  expect(next.editor.title).toBe("");
  expect(next.editor.titleSize).toBe(newDraft().editor.titleSize);
  expect(next.editor.titleInDuration).toBe(0.5);
  expect(next.editor.subtitleIn).toBe(asset("in").id);
  expect(targetEffectKeys("title").every((field) => !next.editor[field])).toBe(true);
  const restored = applyAsset(next, asset("flower"), "title").draft;
  expect(restored.editor.title).toBe(newDraft().editor.title);
  expect(appliedTargets(restored)).toEqual(["title", "subtitle"]);
});

// 场景：有效动画时长保留；取消无效时长后可以保存。
test.each([NaN, 0, 4, 0.1, 3])("清除动画处理时长 %s", (duration) => {
  const draft = applyAsset(newDraft(), asset("in"), "title").draft;
  draft.editor.titleInDuration = duration;
  const next = changeEffects(draft, { titleIn: "" });
  expect(next.editor.titleInDuration).toBe(duration >= 0.1 && duration <= 3 ? duration : 0.5);
  expect(next.editor.titleIn).toBe("");
});

// 场景：各文字对象的有效自定义值、空文字和无效数字均可重置；其他对象、输入草稿和默认配置保持不变。
test.each([
  ["title", false], ["subtitle", false], ["bubble", false],
  ["title", true], ["subtitle", true], ["bubble", true],
] as const)("重置 %s 的全部参数，包含无效输入：%s", (role, invalid) => {
  let draft = newDraft();
  draft.name = "保留模板名称";
  draft.description = "保留模板描述";
  draft.transition_duration_seconds = 2;
  for (const target of ["title", "subtitle", "bubble"] as const) {
    draft = applyAsset(draft, asset(target === "bubble" ? "bubble" : "flower"), target).draft;
    draft = applyAsset(draft, asset("in"), target).draft;
    draft = applyAsset(draft, asset("out"), target).draft;
    draft.editor[target === "bubble" ? "bubbleText" : target] = `自定义 ${target}`;
    draft.editor[`${target}Size`] = 59;
    draft.editor[`${target}X`] = 17;
    draft.editor[`${target}Y`] = 61;
    draft.editor[`${target}InDuration`] = 1.7;
    draft.editor[`${target}OutDuration`] = 2.3;
  }
  draft = applyAsset(draft, asset("filter"), "title").draft;
  const textKey = role === "bubble" ? "bubbleText" : role;
  if (invalid) {
    draft.editor[textKey] = "";
    draft.editor[`${role}Size`] = NaN;
    draft.editor[`${role}X`] = NaN;
    draft.editor[`${role}Y`] = NaN;
    draft.editor[`${role}InDuration`] = NaN;
    draft.editor[`${role}OutDuration`] = 4;
  }
  const original = structuredClone(draft);
  const defaults = { ...defaultEditor };
  const next = resetTextTarget(draft, role);
  expect(next.editor[textKey]).toBe(defaultEditor[textKey]);
  expect(next.editor[`${role}Size`]).toBe(defaultEditor[`${role}Size`]);
  expect(next.editor[`${role}X`]).toBe(defaultEditor[`${role}X`]);
  expect(next.editor[`${role}Y`]).toBe(defaultEditor[`${role}Y`]);
  expect(next.editor[`${role}InDuration`]).toBe(0.5);
  expect(next.editor[`${role}OutDuration`]).toBe(0.5);
  expect(targetEffectKeys(role).every((field) => next.editor[field] === "")).toBe(true);
  const ownFields = new Set<string>([textKey, `${role}Size`, `${role}X`, `${role}Y`, `${role}InDuration`, `${role}OutDuration`, ...targetEffectKeys(role)]);
  expect(Object.entries(next.editor).filter(([key]) => !ownFields.has(key)))
    .toEqual(Object.entries(original.editor).filter(([key]) => !ownFields.has(key)));
  expect(next.name).toBe(original.name);
  expect(next.description).toBe(original.description);
  expect(next.transition_duration_seconds).toBe(original.transition_duration_seconds);
  expect(draft).toEqual(original);
  expect(defaultEditor).toEqual(defaults);
  expect(resetTextTarget(next, role)).toEqual(next);
});

// 场景：循环动画单独启用时同样被重置，随后可以重新应用入场动画。
test("重置循环动画后允许重新选择入场", () => {
  const draft = applyAsset(newDraft(), asset("loop"), "title").draft;
  const reset = resetTextTarget(draft, "title");
  expect(reset.editor.titleLoop).toBe("");
  expect(applyAsset(reset, asset("in"), "title").draft.editor.titleIn).toBe(asset("in").id);
});

// 场景：移除滤镜、特效和转场只清除指定效果，空对象列表与历史未知效果仍可管理。
test("独立移除画面效果并管理空对象和历史效果", () => {
  let draft = newDraft();
  for (const category of ["filter", "vfx/normal", "transition/normal"] as const)
    draft = applyAsset(draft, asset(category), "title").draft;
  draft.transition_duration_seconds = NaN;
  const next = removeTarget(draft, "transition");
  expect(next.transition_duration_seconds).toBe(0.5);
  expect(next.editor.filter).toBe(draft.editor.filter);
  expect(next.editor.vfx).toBe(draft.editor.vfx);
  for (const target of appliedTargets(next)) draft = removeTarget(draft, target);
  draft = removeTarget(draft, "transition");
  expect(appliedTargets(draft)).toEqual([]);
  draft.editor.titleFlower = "flower/unknown-history";
  expect(appliedTargets(draft)).toEqual(["title"]);
  expect(appliedTargets(removeTarget(draft, "title"))).toEqual([]);
});
