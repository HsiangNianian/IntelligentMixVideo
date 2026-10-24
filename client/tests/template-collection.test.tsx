/** 主页云端模板列表、摘要与删除确认测试；真实组件调用存储边界，隔离 HTTP 与桌面 IPC。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Tooltip } from "radix-ui";
import { TemplateCollection, TemplateHome, type TemplateSelection } from "@/features/templates/TemplateHome";
import { jsonListResponse, savedTemplate, templateSummary } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 场景：无效模板只可进入删除确认，取消后仍可选择同库有效模板。
test("无效记录显示原因且不能打开，删除确认保留有效模板", () => {
  const valid = templateSummary(savedTemplate());
  const invalid = { ...valid, template_id: "invalid-template", name: "无效模板", validation_error: "转场必须指定视频交界处", track_count: undefined };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection collection={{ templates: [valid, invalid], loading: false, error: "", refresh: () => {} }} onCreate={() => {}}
    onSelect={(selection) => selections.push(selection)} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "选择模板：无效模板" }).disabled).toBe(true);
  expect(screen.getByText(invalid.validation_error)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除模板：无效模板" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain(invalid.template_id);
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: `选择模板：${valid.name}` }));
  expect(selections).toEqual([{ templateId: valid.template_id }]);
});

// 场景：有效模板显示 UUID，取消不删除，确认失败可重试，成功后刷新列表。
test("模板确认删除且失败保留重试入口", async () => {
  const template = templateSummary(savedTemplate());
  const removed: TemplateSelection[] = [];
  const requests: unknown[] = [];
  let refreshed = 0;
  let fail = true;
  fetchMock.mockImplementation((async (url, init) => {
    requests.push({ url: String(url), method: init?.method });
    return fail ? new Response(JSON.stringify({ detail: "测试删除失败" }), { status: 503 }) : new Response(null, { status: 204 });
  }) as typeof fetch);
  render(<TemplateCollection collection={{ templates: [template], loading: false, error: "", refresh: () => refreshed++ }} onCreate={() => {}}
    onSelect={() => { throw new Error("删除不能打开模板"); }} onDelete={(selection) => removed.push(selection)} />);
  fireEvent.click(screen.getByRole("button", { name: `删除模板：${template.name}` }));
  expect(screen.getByRole("dialog").textContent).toContain(template.name);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: `删除模板：${template.name}` }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByText(/测试删除失败/);
  expect(refreshed).toBe(0);
  expect(removed).toHaveLength(0);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "重试删除" }));
  await waitFor(() => expect(removed).toHaveLength(1));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(refreshed).toBe(1);
  expect(removed).toEqual([{ templateId: template.template_id }]);
  expect(requests).toEqual([1, 2].map(() => ({ url: `http://api.test:8000/template/${template.template_id}`, method: "DELETE" })));
});

// 场景：桌面客户端主页只列出和新建云端模板，没有本地模板的入口、页签或 IPC 读取。
test("主页只提供云端模板", async () => {
  const ipc: string[] = [];
  mockDesktop(async (command) => { ipc.push(command); throw new Error("意外 IPC 请求"); });
  fetchMock.mockImplementation((async (_url, _init) => jsonListResponse([{ ...savedTemplate(), name: "共享模板" }])) as typeof fetch);
  const selections: TemplateSelection[] = [];
  render(<Tooltip.Provider><TemplateHome onSelect={(value) => selections.push(value)} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("button", { name: "选择模板：共享模板" });
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(screen.queryByRole("button", { name: "新建本地模板" })).toBeNull();
  expect(screen.queryAllByRole("tab")).toHaveLength(0);
  expect(screen.queryByText(/本地模板/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  const dialog = screen.getByRole("dialog", { name: "新建云端模板" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "模板名称" }), { target: { value: "  新模板  " } });
  fireEvent.click(within(dialog).getByRole("button", { name: "进入编辑" }));
  expect(selections).toEqual([{ templateId: null, name: "新模板", description: "" }]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(ipc).toHaveLength(0);
});

// 场景：云端读取失败显示原因与重试入口，重试成功后显示模板。
test("云端读取失败可重试", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "云端暂不可用" }), { status: 503 }));
  fetchMock.mockResolvedValueOnce(jsonListResponse([savedTemplate()]));
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByText("云端模板读取失败");
  expect(screen.getByText("云端暂不可用")).toBeTruthy();
  expect(screen.getByLabelText("模板数量").textContent).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await screen.findByRole("button", { name: `选择模板：${savedTemplate().name}` });
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
