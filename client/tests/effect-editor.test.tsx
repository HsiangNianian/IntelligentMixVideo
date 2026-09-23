/** 参数面板核心测试：验证文字字段更新和重置按钮输出；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { EffectEditor } from "@/features/templates/EffectEditor";
import { defaultEditor, type EffectDraft as Draft, type TextRole } from "@/features/templates/model";
import { effectDraft as newDraft } from "./fixtures";
import { readCatalog } from "@/features/templates/sdk";

const catalog = readCatalog();

/** 保存组件输出并重新提供受控值，返回最新草稿供断言检查。 */
function renderEditor(initial: Draft, target: TextRole) {
  let draft = initial;
  const update = (next: Draft) => {
    draft = next;
    view.rerender(<EffectEditor draft={draft} target={target} catalog={catalog} onChange={update} onClose={() => {}} />);
  };
  const view = render(<EffectEditor draft={draft} target={target} catalog={catalog} onChange={update} onClose={() => {}} />);
  return () => draft;
}

// 场景：三种文字对象的输入分别更新自身字段，保留其他参数和原草稿。
test.each(["title", "subtitle", "bubble"] as TextRole[])("%s 的控件更新对应文字参数", (role) => {
  const original = newDraft();
  const read = renderEditor(original, role);
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "新的示例文字" } });
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "59" } });
  fireEvent.change(screen.getByLabelText("水平位置 %"), { target: { value: "25.5" } });
  fireEvent.change(screen.getByLabelText("垂直位置 %"), { target: { value: "72" } });
  expect(read()).toEqual({ ...original, editor: {
    ...original.editor, [role === "bubble" ? "bubbleText" : role]: "新的示例文字",
    [`${role}Size`]: 59, [`${role}X`]: 25.5, [`${role}Y`]: 72,
  } });
  expect(original.editor).toEqual(defaultEditor);
});

// 场景：点击重置按钮更新受控草稿与输入，其他对象参数保持不变。
test("重置按钮恢复当前文字对象", () => {
  const initial = newDraft();
  initial.editor.subtitle = "保留字幕";
  const draft = { ...initial, editor: { ...initial.editor, title: "自定义标题", titleSize: 59, titleIn: "in/fade_in" } };
  const read = renderEditor(draft, "title");
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(read()).toEqual(initial);
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe(defaultEditor.title);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe(String(defaultEditor.titleSize));
});
