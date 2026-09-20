/** 参数面板组件测试：使用真实目录和受控 React 控件验证字段编辑、重置与动画互斥；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { EffectEditor } from "@/features/templates/EffectEditor";
import { defaultEditor, type EffectDraft as Draft, type TextRole } from "@/features/templates/model";
import { effectDraft as newDraft } from "./fixtures";
import type { EffectTarget } from "@/features/templates/effects";
import { readCatalog } from "@/features/templates/sdk";

const catalog = readCatalog();

/** 保存组件实际输出的草稿并重新提供受控值，不替代任何编辑规则。 */
function renderEditor(initial: Draft, target: EffectTarget) {
  let draft = initial;
  const update = (next: Draft) => {
    draft = next;
    view.rerender(<EffectEditor draft={draft} target={target} catalog={catalog} onChange={update} onClose={() => {}} />);
  };
  const view = render(<EffectEditor draft={draft} target={target} catalog={catalog} onChange={update} onClose={() => {}} />);
  return () => draft;
}

/** 通过 Radix 的真实键盘交互选择选项。 */
async function select(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: option }), { key: "Enter" });
}

// 场景：三种文字对象的输入写入各自字段，支持位置边界与小数，其他参数和原草稿保持不变。
test.each(["title", "subtitle", "bubble"] as TextRole[])("%s 的文字与数值控件更新正确字段", (role) => {
  const original = newDraft();
  const read = renderEditor(original, role);
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "新的示例文字" } });
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "120" } });
  fireEvent.change(screen.getByLabelText("水平位置 %"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("垂直位置 %"), { target: { value: "100" } });
  expect(read()).toEqual({ ...original, editor: {
    ...original.editor, [role === "bubble" ? "bubbleText" : role]: "新的示例文字",
    [`${role}Size`]: 120, [`${role}X`]: 0, [`${role}Y`]: 100,
  } });
  fireEvent.change(screen.getByLabelText("水平位置 %"), { target: { value: "25.5" } });
  expect(read().editor[`${role}X`]).toBe(25.5);
  expect(original.editor).toEqual(defaultEditor);
});

// 场景：重置按钮恢复文字参数和时长，清除所选效果并修复空数字，保留其他参数。
test.each(["title", "subtitle", "bubble"] as TextRole[])("%s 重置按钮恢复控件和草稿，重复操作结果一致", (role) => {
  const original = newDraft();
  original.editor.filter = catalog.find((asset) => asset.category === "filter")!.id;
  const textKey = role === "bubble" ? "bubbleText" : role;
  const styleKey = role === "bubble" ? "bubble" : `${role}Flower`;
  const draft: Draft = { ...original, editor: { ...original.editor,
    [textKey]: "自定义文字", [`${role}Size`]: 59, [`${role}X`]: 17, [`${role}Y`]: 61,
    [styleKey]: catalog.find((asset) => asset.category === (role === "bubble" ? "bubble" : "flower"))!.id,
    [`${role}In`]: catalog.find((asset) => asset.category === "in")!.id,
    [`${role}Out`]: catalog.find((asset) => asset.category === "out")!.id,
    [`${role}InDuration`]: 1.7, [`${role}OutDuration`]: 2.3,
  } };
  const read = renderEditor(draft, role);
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "" } });
  expect(Number.isNaN(read().editor[`${role}Size`])).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(read()).toEqual(original);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe(defaultEditor[textKey]);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe(String(defaultEditor[`${role}Size`]));
  expect(screen.getByLabelText<HTMLInputElement>("水平位置 %").value).toBe(String(defaultEditor[`${role}X`]));
  expect(screen.getByLabelText<HTMLInputElement>("垂直位置 %").value).toBe(String(defaultEditor[`${role}Y`]));
  for (const duration of screen.getAllByLabelText<HTMLInputElement>("时长 / 秒")) {
    expect(duration.value).toBe("0.5");
    expect(duration.disabled).toBe(true);
  }
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(read()).toEqual(original);
  expect(draft.editor[`${role}Size`]).toBe(59);
});

// 场景：通过实际选择器启用、编辑和清除动画，互斥状态及无效时长随选择更新。
test("动画选择器约束互斥关系并恢复无效时长", async () => {
  const read = renderEditor(newDraft(), "title");
  const entry = catalog.find((asset) => asset.category === "in")!;
  const loop = catalog.find((asset) => asset.category === "loop")!;
  await select("入场动画", entry.name);
  expect(read().editor.titleIn).toBe(entry.id);
  expect(screen.getByRole<HTMLButtonElement>("combobox", { name: "循环动画" }).disabled).toBe(true);
  const duration = screen.getAllByLabelText<HTMLInputElement>("时长 / 秒")[0];
  expect(duration.disabled).toBe(false);
  fireEvent.change(duration, { target: { value: "1.7" } });
  expect(read().editor.titleInDuration).toBe(1.7);
  fireEvent.change(duration, { target: { value: "" } });
  expect(Number.isNaN(read().editor.titleInDuration)).toBe(true);
  await select("入场动画", "无效果");
  expect(read().editor.titleInDuration).toBe(0.5);
  await select("循环动画", loop.name);
  expect(read().editor.titleLoop).toBe(loop.id);
  for (const label of ["入场动画", "出场动画"]) {
    expect(screen.getByRole<HTMLButtonElement>("combobox", { name: label }).disabled).toBe(true);
  }
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(read()).toEqual(newDraft());
  await select("入场动画", entry.name);
  expect(read().editor.titleIn).toBe(entry.id);
});

// 场景：参数面板更新转场时长，保留其余参数；空输入继续保留待校验状态。
test("转场数值编辑保持文字和效果数据", () => {
  const initial = newDraft();
  const read = renderEditor(initial, "transition");
  const input = screen.getByLabelText<HTMLInputElement>("转场时长 / 秒");
  for (const value of ["0.1", "3"]) {
    fireEvent.change(input, { target: { value } });
    expect(read()).toEqual({ ...initial, transition_duration_seconds: Number(value) });
  }
  fireEvent.change(input, { target: { value: "" } });
  expect(Number.isNaN(read().transition_duration_seconds)).toBe(true);
  expect(read().editor).toEqual(initial.editor);
  expect(input.value).toBe("");
});
