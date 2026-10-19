/** 模板页签筛选、摘要与删除确认测试；真实组件调用存储边界，隔离 HTTP 和本地 IPC。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Tooltip } from "radix-ui";
import { TemplateCollection, TemplateHome, type TemplateSelection } from "@/features/templates/TemplateHome";
import { protobufListResponse, savedTemplate, templateSummary } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 场景：无效模板只可进入删除确认，取消后仍可选择同库有效模板。
test("无效记录显示原因且不能打开，删除确认保留有效模板", () => {
  const valid = templateSummary(savedTemplate());
  const invalid = { ...valid, template_id: "invalid-template", name: "无效模板", validation_error: "转场必须指定视频交界处", track_count: undefined };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection collections={[{ environment: "cloud",
    collection: { templates: [valid, invalid], loading: false, error: "", unavailable: false, refresh: () => {} } }]}
    onSelect={(selection) => selections.push(selection)} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "选择模板：无效模板" }).disabled).toBe(true);
  expect(screen.getByText(invalid.validation_error)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除模板：无效模板" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain(invalid.template_id);
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: `选择模板：${valid.name}` }));
  expect(selections).toEqual([{ environment: "cloud", templateId: valid.template_id }]);
});

// 场景：两库有效模板都显示 UUID，取消不删除，确认失败可重试；本地操作保留所属库。
test.each(["cloud", "local"] as const)("%s 模板确认删除且失败保留重试入口", async (environment) => {
  const template = { ...templateSummary(savedTemplate()), library: "/tmp/test-templates.json" };
  const removed: TemplateSelection[] = [];
  const requests: unknown[] = [];
  let refreshed = 0;
  let fail = true;
  if (environment === "local") mockDesktop(async (command, args) => {
    requests.push({ command, ...args });
    if (fail) throw "测试删除失败";
  });
  else fetchMock.mockImplementation((async (url, init) => {
    requests.push({ url: String(url), method: init?.method });
    return fail ? new Response(JSON.stringify({ detail: "测试删除失败" }), { status: 503 }) : new Response(null, { status: 204 });
  }) as typeof fetch);
  render(<TemplateCollection collections={[{ environment, collection: { templates: [template], loading: false, error: "", unavailable: false, refresh: () => refreshed++ } }]}
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
  expect(removed).toEqual([{ environment, templateId: template.template_id }]);
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(environment === "local"
    ? { command: "local_templates", operation: "delete", id: template.template_id, library: template.library, draft: undefined }
    : { url: `http://api.test:8000/template/${template.template_id}`, method: "DELETE" });
});

// 场景：默认合并两库，同 ID 仍是两张卡片；切换页签过滤列表并保留打开目标的真实来源。
test("全部、云端、本地页签筛选模板且不串库", async () => {
  const cloud = { ...savedTemplate(), name: "共享模板" };
  const local = { ...templateSummary(cloud), name: "本机模板" };
  let localReads = 0;
  mockDesktop(async (command, args) => {
    if (command === "local_templates" && args.operation === "list") { localReads++; return [local]; }
    throw new Error("意外 IPC 请求");
  });
  fetchMock.mockImplementation((async (_url, _init) => protobufListResponse([cloud])) as typeof fetch);
  const selections: TemplateSelection[] = [];
  render(<Tooltip.Provider><TemplateHome onSelect={(value) => selections.push(value)} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("button", { name: "选择模板：共享模板" });
  await screen.findByRole("button", { name: "选择模板：本机模板" });
  expect(screen.getByRole("tab", { name: "全部模板", selected: true })).toBeTruthy();
  expect(screen.getByLabelText("模板数量").textContent).toBe("2");
  fireEvent.mouseDown(screen.getByRole("tab", { name: "云端模板" }), { button: 0, ctrlKey: false });
  expect(screen.getByRole("tabpanel", { name: "云端模板" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "选择模板：本机模板" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "选择模板：共享模板" }));
  fireEvent.mouseDown(screen.getByRole("tab", { name: "本地模板" }), { button: 0, ctrlKey: false });
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(screen.queryByRole("button", { name: "选择模板：共享模板" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "选择模板：本机模板" }));
  expect(selections).toEqual([{ environment: "cloud", templateId: cloud.template_id }, { environment: "local", templateId: local.template_id }]);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "全部模板" }), { button: 0, ctrlKey: false });
  expect(screen.getAllByRole("button", { name: /^选择模板：/ })).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(localReads).toBe(1);
});

// 场景：浏览器的全部列表不插入本地不可用提示，点击本地页签才显示说明。
test("浏览器仅在本地页签提示桌面端限制", async () => {
  fetchMock.mockImplementation((async (_url, _init) => protobufListResponse([savedTemplate()])) as typeof fetch);
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("button", { name: /^选择模板：/ });
  expect(screen.queryByText("本地模板需要桌面客户端")).toBeNull();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "本地模板" }), { button: 0, ctrlKey: false });
  expect(screen.getByText("本地模板需要桌面客户端")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^选择模板：/ })).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "刷新模板" }).disabled).toBe(true);
});

// 场景：云端断线不隐藏已读出的本地模板，错误保留单独重试入口。
test("全部模板中一库失败不影响另一库", async () => {
  mockDesktop(async () => [templateSummary({ ...savedTemplate(), name: "离线模板" })]);
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "云端暂不可用" }), { status: 503 }));
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("button", { name: "选择模板：离线模板" });
  await screen.findByText("云端模板读取失败");
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
});
