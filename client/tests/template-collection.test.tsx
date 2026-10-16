/** 模板摘要与删除确认测试；真实组件调用存储边界，隔离 HTTP 和本地 IPC。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { TemplateCollection, type TemplateSelection } from "@/features/templates/TemplateHome";
import { savedTemplate, templateSummary } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 场景：无效模板只可进入删除确认，取消后仍可选择同库有效模板。
test("无效记录显示原因且不能打开，删除确认保留有效模板", () => {
  const valid = templateSummary(savedTemplate());
  const invalid = { ...valid, template_id: "invalid-template", name: "无效模板", validation_error: "转场必须指定视频交界处", track_count: undefined };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection environment="cloud"
    collection={{ templates: [valid, invalid], loading: false, error: "", unavailable: false, refresh: () => {} }}
    onSelect={(selection) => selections.push(selection)} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "选择模板：无效模板" }).disabled).toBe(true);
  expect(screen.getByText(invalid.validation_error)).toBeTruthy();
  expect(screen.getByText(`styleid：${invalid.template_id}`)).toBeTruthy();
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
  render(<TemplateCollection environment={environment} collection={{ templates: [template], loading: false, error: "", unavailable: false, refresh: () => refreshed++ }}
    onSelect={() => { throw new Error("删除不能打开模板"); }} onDelete={(selection) => removed.push(selection)} />);
  expect(screen.getByText(`styleid：${template.template_id}`)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: `删除模板：${template.name}` }));
  expect(screen.getByRole("dialog").textContent).toContain(`是否确认删除「${template.name}」模板`);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: `删除模板：${template.name}` }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByText(/测试删除失败/);
  expect(refreshed).toBe(0);
  expect(removed).toHaveLength(0);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(removed).toHaveLength(1));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(refreshed).toBe(1);
  expect(removed).toEqual([{ environment, templateId: template.template_id }]);
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(environment === "local"
    ? { command: "local_templates", operation: "delete", id: template.template_id, library: template.library, draft: undefined }
    : { url: `http://api.test:8000/template/${template.template_id}`, method: "DELETE" });
});
