/** 参数面板核心测试：验证文字字段更新、重置按钮、关键词切换与开关、数值滑块和可搜索特效下拉框；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { EffectEditor } from "@/features/templates/EffectEditor";
import { defaultEditor, effectGroups, type EffectDraft as Draft, type TextRole } from "@/features/templates/model";
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
    fireEvent.click(screen.getByRole("button", { name: label }));
  }
  expect(read().editor).toMatchObject({ subtitleKeywordBold: true, subtitleKeywordItalic: true,
    subtitleKeywordUnderline: true, subtitleKeywordStrikeout: true });
  fireEvent.click(screen.getByRole("switch", { name: "设置关键词颜色" }));
  const color = screen.getByLabelText<HTMLInputElement>("关键词颜色");
  fireEvent.change(color, { target: { value: "#123456" } });
  expect(read().editor.subtitleKeywordColor).toBe("#123456");
  fireEvent.click(screen.getByRole("switch", { name: "设置关键词字号" }));
  fireEvent.change(screen.getByLabelText("关键词字号"), { target: { value: "64" } });
  expect(read().editor.subtitleKeywordSize).toBe(64);
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  expect(screen.getByRole("button", { name: "加粗" }).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByRole("switch", { name: "设置关键词颜色" }).getAttribute("aria-checked")).toBe("false");
  expect(read().editor.subtitleKeywordSize).toBe(0);
});

// 场景：顶部标题显示关键词样式，修改示例文字后能够设置颜色和加粗。
test("顶部标题可设置关键词样式", () => {
  const initial = newDraft();
  initial.editor.titleKeyword = "旧词";
  const read = renderEditor(initial, "title");
  expect(screen.queryByRole("textbox", { name: "指定关键词" })).toBeNull();
  expect(read().editor.titleKeyword).toBe("旧词");
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "示例标题" } });
  fireEvent.click(screen.getByRole("button", { name: "加粗" }));
  fireEvent.click(screen.getByRole("switch", { name: "设置关键词颜色" }));
  fireEvent.change(screen.getByLabelText("关键词颜色"), { target: { value: "#123456" } });
  fireEvent.click(screen.getByRole("switch", { name: "设置关键词字号" }));
  fireEvent.change(screen.getByLabelText("关键词字号"), { target: { value: "72" } });
  expect(read().editor).toMatchObject({ titleKeyword: "", titleKeywordBold: true,
    titleKeywordColor: "#123456", titleKeywordSize: 72 });
});

// 场景：花字下拉框按编号搜索后选择，写入当前对象并关闭浮层；再选「无效果」清除。
test("可搜索下拉框按编号过滤并选择花字", async () => {
  const flowers = catalog.filter((asset) => asset.category === effectGroups.titleFlower);
  const target = flowers[flowers.length - 1];
  const read = renderEditor(newDraft(), "title");
  fireEvent.click(screen.getByRole("combobox", { name: "花字样式" }));
  fireEvent.change(await screen.findByPlaceholderText("搜索花字样式"), { target: { value: target.effect_id } });
  const options = screen.getAllByRole("option");
  expect(options.some((option) => option.textContent?.includes(flowers[0].effect_id) && flowers[0].effect_id !== target.effect_id)).toBe(false);
  fireEvent.click(screen.getByRole("option", { name: new RegExp(target.effect_id) }));
  expect(read().editor.titleFlower).toBe(target.id);
  expect(screen.queryByPlaceholderText("搜索花字样式")).toBeNull();
  expect(screen.getByRole("combobox", { name: "花字样式" }).textContent).toBe(target.name);
  fireEvent.click(screen.getByRole("combobox", { name: "花字样式" }));
  fireEvent.click(await screen.findByRole("option", { name: /^无效果/ }));
  expect(read().editor.titleFlower).toBe("");
});

// 场景：字号滑块与输入框共享数值，键盘拖动滑块后输入框和草稿同步；超出范围的输入不移动滑块越界。
test("数值滑块与输入框同步字号", () => {
  const read = renderEditor(newDraft(), "title");
  const slider = screen.getByRole("slider", { name: "字号滑块" });
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(read().editor.titleSize).toBe(defaultEditor.titleSize + 1);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe(String(defaultEditor.titleSize + 1));
  fireEvent.keyDown(slider, { key: "End" });
  expect(read().editor.titleSize).toBe(300);
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "48" } });
  expect(slider.getAttribute("aria-valuenow")).toBe("48");
});
