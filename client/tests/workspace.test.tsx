/** 工作区核心交互：真实表单、选择器和弹窗，只替换 SDK 预览及 HTTP；执行 bun run test。 */
import { expect, mock, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import type { Draft, EffectAsset } from "@/features/templates/model";
import { catalog, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

// SDK 依赖视频、字体和硬件加速；本组只验证目录回传和最新草稿传给预览的行为。
mock.module("@/features/templates/TemplatePreview", () => ({
  /** 用轻量输出替代第三方播放器，保留工作区实际使用的输入与目录回调。 */
  TemplatePreview({ draft, onCatalog }: { draft: Draft; onCatalog: (items: EffectAsset[]) => void }) {
    useEffect(() => onCatalog(catalog), [onCatalog]);
    return <output aria-label="预览标题">{draft.editor.title}</output>;
  },
}));
const { TemplateWorkspace } = await import("@/features/templates/TemplateWorkspace");

/** 通过真实 Radix 控件的键盘交互选择选项，不替换基础 UI 组件。 */
async function choose(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: option }), { key: "Enter" });
}

/** 从列表打开服务端最新详情，后续测试从干净的已保存模板开始。 */
async function openExistingTemplate() {
  const saved = savedTemplate();
  fetchMock.mockResolvedValueOnce(Response.json([saved]));
  render(<TemplateWorkspace />);
  await screen.findByText("共享模板库 · 1 个模板");
  fetchMock.mockResolvedValueOnce(Response.json(saved));
  await choose("打开模板", saved.name);
  await screen.findByDisplayValue(saved.name);
  return saved;
}

// 测试新建时选择效果、编辑内容传给预览，保存成功清除脏状态，再保存时更新同一 ID。
test("创建、预览草稿与更新模板", async () => {
  fetchMock.mockResolvedValueOnce(Response.json([]));
  render(<TemplateWorkspace />);
  await screen.findByText("共享模板库 · 0 个模板");
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "我的模板" } });
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "新标题" } });
  await choose("入场动画", "淡入");
  expect(screen.getByRole("combobox", { name: "循环动画" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByLabelText("预览标题").textContent).toBe("新标题");
  const saved = savedTemplate();
  saved.name = "我的模板";
  saved.editor.title = "新标题";
  fetchMock.mockResolvedValueOnce(Response.json(saved, { status: 201 }));
  // Happy DOM 对小数 step 的原生校验不准确；直接触发表单提交，数值校验另由核心逻辑测试覆盖。
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("模板「我的模板」已保存");
  expect(screen.queryByText(/有未保存的修改/)).toBeNull();
  const created = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
  expect(created).not.toHaveProperty("template_id");
  expect(created.editor.titleIn).toBe("in/fade_in");
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "更新模板" } });
  fetchMock.mockResolvedValueOnce(Response.json({ ...saved, name: "更新模板" }));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("模板「更新模板」已保存");
  expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body)).template_id).toBe(saved.template_id);
  expect(screen.getByText("共享模板库 · 1 个模板")).toBeTruthy();
});

// 测试未保存切换时取消保留草稿，放弃修改才清空并切换到新建模板。
test("取消和放弃未保存修改", async () => {
  await openExistingTemplate();
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "未保存名称" } });
  fireEvent.click(screen.getByRole("button", { name: "新建模板" }));
  const dialog = await screen.findByRole("dialog", { name: "保存当前修改？" });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByDisplayValue("未保存名称")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "新建模板" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
  expect(screen.queryByText(/有未保存的修改/)).toBeNull();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

// 测试“保存并切换”失败时保留弹窗和草稿，重试成功后才完成切换。
test("保存失败保留草稿，重试后切换", async () => {
  const saved = await openExistingTemplate();
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "修改后" } });
  fireEvent.click(screen.getByRole("button", { name: "新建模板" }));
  const dialog = await screen.findByRole("dialog");
  fetchMock.mockResolvedValueOnce(Response.json({ detail: "数据库暂不可用" }, { status: 503 }));
  await act(async () => {
    fireEvent.click(within(dialog).getByRole("button", { name: "保存并切换" }));
  });
  await within(dialog).findByText("数据库暂不可用");
  expect(screen.getByDisplayValue("修改后")).toBeTruthy();
  expect(screen.queryByText("模板「修改后」已保存")).toBeNull();
  fetchMock.mockResolvedValueOnce(Response.json({ ...saved, name: "修改后" }));
  await act(async () => {
    fireEvent.click(within(dialog).getByRole("button", { name: "保存并切换" }));
  });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
  expect(screen.getByText("模板「修改后」已保存")).toBeTruthy();
});

// 测试另存为提交新名称且不带原 ID，原模板仍在列表中。
test("另存为创建独立模板", async () => {
  await openExistingTemplate();
  fireEvent.click(screen.getByRole("button", { name: "另存为" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("新名称"), { target: { value: "副本" } });
  fetchMock.mockResolvedValueOnce(Response.json({ ...savedTemplate(), name: "副本", template_id: "copy-id" }, { status: 201 }));
  fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));
  await screen.findByText("模板「副本」已保存");
  const body = JSON.parse(String(fetchMock.mock.calls[2][1]?.body));
  expect(body.name).toBe("副本");
  expect(body).not.toHaveProperty("template_id");
  expect(screen.getByText("共享模板库 · 2 个模板")).toBeTruthy();
});

// 测试删除必须确认，取消不发请求，成功后清除当前模板并更新列表。
test("确认后才删除模板", async () => {
  const saved = await openExistingTemplate();
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "取消" }));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "确认删除" }));
  await screen.findByText("模板已删除");
  expect(fetchMock.mock.calls[2][0]).toBe(`http://api.test:8000/template/${saved.template_id}`);
  expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
  expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
  expect(screen.getByText("共享模板库 · 0 个模板")).toBeTruthy();
});
