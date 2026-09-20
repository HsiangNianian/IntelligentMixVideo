/** 资产浏览组件测试：使用随包目录验证搜索、分页、作用对象和动画限制；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { EffectAssets } from "@/features/templates/EffectAssets";
import { applyAsset, type EffectTarget } from "@/features/templates/effects";
import { type EffectDraft as Draft, type TextRole } from "@/features/templates/model";
import { effectDraft as newDraft } from "./fixtures";
import { readCatalog } from "@/features/templates/sdk";

const catalog = readCatalog();

/** 保留真实组件输出并更新受控属性，用于检查所选对象和完整草稿。 */
function renderAssets(initial = newDraft(), initialTarget: TextRole = "title") {
  let draft = initial;
  let textTarget = initialTarget;
  let selected: EffectTarget | null = null;
  const onTextTarget = (next: TextRole) => { textTarget = next; refresh(); };
  const onApply = (next: Draft, target: EffectTarget) => { draft = next; selected = target; refresh(); };
  const element = () => <EffectAssets editor={draft.editor} catalog={catalog} textTarget={textTarget} onTextTarget={onTextTarget} onAsset={(asset, role) => {
    const result = applyAsset(draft, asset, role);
    onApply(result.draft, result.target);
  }} />;
  const view = render(element());
  const refresh = () => view.rerender(element());
  return { read: () => ({ draft, selected }), replace: (next: Draft) => { draft = next; refresh(); } };
}

// 场景：真实目录按名称和编号搜索，忽略编号大小写及首尾空白，无结果后可恢复完整目录。
test("资产搜索支持名称、编号和空结果恢复，回车被阻止", () => {
  const initial = newDraft();
  const { read } = renderAssets(initial);
  const item = catalog.find((asset) => asset.category === "flower")!;
  const search = screen.getByRole("searchbox", { name: "搜索特效" });
  for (const value of [item.name, `  ${item.effect_id.toLocaleLowerCase()}  `]) {
    fireEvent.change(search, { target: { value } });
    expect(screen.getByRole("button", { name: `应用花字：${item.name}` })).toBeTruthy();
  }
  fireEvent.change(search, { target: { value: "不存在的特效编号" } });
  expect(screen.getByRole("status").textContent).toBe("没有匹配的特效");
  expect(screen.queryByRole("button", { name: /^应用花字：/ })).toBeNull();
  expect(fireEvent.keyDown(search, { key: "Enter", cancelable: true })).toBe(false);
  fireEvent.change(search, { target: { value: "   " } });
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getAllByRole("button", { name: /^应用花字：/ })).toHaveLength(24);
  expect(read()).toEqual({ draft: initial, selected: null });
});

// 场景：资产每次增加 24 项，搜索和分类切换均恢复初始展示数量。
test("资产分页在搜索和分类切换后重新计数", () => {
  renderAssets();
  expect(catalog.filter((asset) => asset.category === "flower").length).toBeGreaterThan(48);
  fireEvent.click(screen.getByRole("button", { name: "显示更多" }));
  expect(screen.getAllByRole("button", { name: /^应用花字：/ })).toHaveLength(48);
  const search = screen.getByRole("searchbox", { name: "搜索特效" });
  fireEvent.change(search, { target: { value: "不存在的编号" } });
  expect(screen.queryByRole("button", { name: "显示更多" })).toBeNull();
  fireEvent.change(search, { target: { value: "" } });
  expect(screen.getAllByRole("button", { name: /^应用花字：/ })).toHaveLength(24);
  fireEvent.click(screen.getByRole("button", { name: "显示更多" }));
  fireEvent.click(screen.getByRole("button", { name: "滤镜" }));
  fireEvent.click(screen.getByRole("button", { name: "花字" }));
  expect(screen.getAllByRole("button", { name: /^应用花字：/ })).toHaveLength(24);
});

// 场景：选择底部字幕后应用和替换花字，更新字幕效果与选中状态，保留原有文字位置和标题。
test("资产应用到选定文字对象，同类替换保留独立参数", async () => {
  const initial = newDraft();
  initial.editor.subtitle = "自定义字幕";
  initial.editor.subtitleX = 18;
  const { read } = renderAssets(initial);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "底部字幕" }), { key: "Enter" });
  const [first, second] = catalog.filter((asset) => asset.category === "flower");
  for (const item of [first, second, second]) {
    fireEvent.click(screen.getByRole("button", { name: `应用花字：${item.name}` }));
    expect(read()).toEqual({ selected: "subtitle", draft: { ...initial, editor: { ...initial.editor, subtitleFlower: item.id } } });
    expect(screen.getAllByRole("button", { name: /^应用花字：/, pressed: true })).toHaveLength(1);
  }
  expect(screen.getByRole("button", { name: `应用花字：${first.name}` }).getAttribute("aria-pressed")).toBe("false");
  expect(initial.editor.subtitleFlower).toBe("");
});

// 场景：循环与入出场动画冲突时不能应用，清除对应动画后可以重新选择。
test.each(["文字入场", "文字出场", "文字循环"])("%s 资产根据现有动画禁用和恢复", (label) => {
  const initial = newDraft();
  const category = label === "文字循环" ? "in" : "loop";
  initial.editor[label === "文字循环" ? "titleIn" : "titleLoop"] = catalog.find((asset) => asset.category === category)!.id;
  const { read, replace } = renderAssets(initial);
  fireEvent.click(screen.getByRole("button", { name: label }));
  const buttons = screen.getAllByRole<HTMLButtonElement>("button", { name: new RegExp(`^应用${label}：`) });
  expect(buttons.every((button) => button.disabled)).toBe(true);
  fireEvent.click(buttons[0]);
  expect(read()).toEqual({ draft: initial, selected: null });
  replace(newDraft());
  expect(buttons.every((button) => !button.disabled)).toBe(true);
  fireEvent.click(buttons[0]);
  expect(read().selected).toBe("title");
  const field = label === "文字循环" ? "titleLoop" : label === "文字入场" ? "titleIn" : "titleOut";
  const selectedCategory = label === "文字循环" ? "loop" : label === "文字入场" ? "in" : "out";
  expect(read().draft.editor[field]).toBe(catalog.find((asset) => asset.category === selectedCategory)!.id);
});

// 场景：气泡尚无样式时禁止应用动画，添加实际样式后允许应用并保持气泡文字。
test("气泡动画依赖气泡样式，添加样式后解除限制", () => {
  const initial = newDraft();
  const { read, replace } = renderAssets(initial, "bubble");
  fireEvent.click(screen.getByRole("button", { name: "文字入场" }));
  expect(screen.getByRole("status").textContent).toBe("请先添加气泡样式。");
  const button = screen.getAllByRole<HTMLButtonElement>("button", { name: /^应用文字入场：/ })[0];
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(read()).toEqual({ draft: initial, selected: null });
  const bubble = catalog.find((asset) => asset.category === "bubble")!;
  replace({ ...initial, editor: { ...initial.editor, bubble: bubble.id } });
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(button);
  expect(read()).toEqual({ selected: "bubble", draft: { ...initial, editor: {
    ...initial.editor, bubble: bubble.id, bubbleIn: catalog.find((asset) => asset.category === "in")!.id,
  } } });
});
