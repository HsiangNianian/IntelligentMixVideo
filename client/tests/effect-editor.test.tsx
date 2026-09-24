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

// 场景：底部字幕显示关键词样式，开关与颜色选择器更新所属字幕对象。
test("底部字幕可独立选择关键词样式", () => {
  const read = renderEditor(newDraft(), "subtitle");
  for (const label of ["加粗", "斜体", "下划线", "删除线"]) {
    fireEvent.click(screen.getByRole("checkbox", { name: label }));
  }
  expect(read().editor).toMatchObject({ subtitleKeywordBold: true, subtitleKeywordItalic: true,
    subtitleKeywordUnderline: true, subtitleKeywordStrikeout: true });
  fireEvent.click(screen.getByRole("checkbox", { name: "设置关键词颜色" }));
  const color = screen.getByLabelText<HTMLInputElement>("关键词颜色");
  fireEvent.change(color, { target: { value: "#123456" } });
  expect(read().editor.subtitleKeywordColor).toBe("#123456");
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "加粗" }).checked).toBe(false);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "设置关键词颜色" }).checked).toBe(false);
});

// 场景：顶部标题显示关键词样式，修改示例文字后能够设置颜色和加粗。
test("顶部标题可设置关键词样式", () => {
  const initial = newDraft();
  initial.editor.titleKeyword = "旧词";
  const read = renderEditor(initial, "title");
  expect(screen.queryByLabelText("标题关键词")).toBeNull();
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "示例标题" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "加粗" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "设置关键词颜色" }));
  fireEvent.change(screen.getByLabelText("关键词颜色"), { target: { value: "#123456" } });
  expect(read().editor).toMatchObject({ titleKeyword: "", titleKeywordBold: true,
    titleKeywordColor: "#123456" });
});
