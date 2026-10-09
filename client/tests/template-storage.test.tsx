/** 本地库路径切换回归：真实组件与隔离 IPC 验证草稿保护、失败恢复和云端隔离。 */
import { afterEach, expect, mock, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createRef } from "react";
import { toast } from "sonner";
import HomePage from "@/pages/HomePage";
import { PluginSettings } from "@/features/settings/PluginSettings";
import { TemplateWorkspace, type TemplateWorkspaceHandle } from "@/features/templates/TemplateWorkspace";
import { listTemplates } from "@/features/templates/api";
import { apiBase, setApiBase } from "@/lib/api-base";
import { fetchMock, mockDesktop } from "./setup";
import { protobufListResponse, protobufTemplateResponse, savedTemplate } from "./fixtures";

// 每例恢复共享后端地址，避免通用设置保存影响其他 API 用例。
const originalApiBase = apiBase();
afterEach(() => setApiBase(originalApiBase));

/** 请求路径切换但不等待确认弹窗，记录最终错误以避免未处理的 Promise 拒绝。 */
async function requestSwitch(ref: React.RefObject<TemplateWorkspaceHandle | null>, save: () => Promise<void>) {
  let result: Promise<unknown> = Promise.resolve();
  await act(async () => { result = ref.current!.changeStorage(save).catch(error => error); });
  return { result };
}

// 场景：本地或云端读取失败后仍能修改本地路径；成功后只清理本地旧请求。
test.each(["local", "cloud"] as const)("%s 读取失败后允许切换本地路径", async environment => {
  mockDesktop(async () => { throw new Error("文件损坏"); });
  fetchMock.mockRejectedValue(new Error("云端读取失败"));
  const ref = createRef<TemplateWorkspaceHandle>();
  render(<TemplateWorkspace ref={ref} selection={{ environment, templateId: "missing" }} onHome={() => {}} />);
  await screen.findByRole("button", { name: "重试打开模板" });
  const save = mock(async () => {});
  const pending = await requestSwitch(ref, save);
  expect(await pending.result).toBeUndefined();
  expect(save).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "重试打开模板" }) !== null).toBe(environment === "cloud");
});

// 场景：云端进行中的读取不阻止本地换库，也不会被本地换库取消。
test("云端读取中允许本地换库并保留读取结果", async () => {
  const response = Promise.withResolvers<Response>();
  fetchMock.mockReturnValue(response.promise);
  const ref = createRef<TemplateWorkspaceHandle>();
  render(<TemplateWorkspace ref={ref} selection={{ environment: "cloud", templateId: "cloud-id" }} onHome={() => {}} />);
  await screen.findByText("正在读取模板…");
  const save = mock(async () => {});
  const pending = await requestSwitch(ref, save);
  expect(await pending.result).toBeUndefined();
  await act(async () => response.resolve(protobufTemplateResponse(savedTemplate(), "get")));
  expect((await screen.findByLabelText("模板名称")).textContent).toBe("已有模板");
});

// 场景：本地读取进行中阻止换库，避免将迟到的旧库响应应用到新路径。
test("本地读取完成前拒绝换库", async () => {
  const response = Promise.withResolvers<unknown>();
  mockDesktop(async () => response.promise);
  const ref = createRef<TemplateWorkspaceHandle>();
  render(<TemplateWorkspace ref={ref} selection={{ environment: "local", templateId: "local-id" }} onHome={() => {}} />);
  await screen.findByText("正在读取模板…");
  const save = mock(async () => {});
  const pending = await requestSwitch(ref, save);
  expect(String(await pending.result)).toContain("等待本地模板操作");
  expect(save).not.toHaveBeenCalled();
  await act(async () => response.resolve({ data: savedTemplate(), warning: null }));
});

// 场景：取消保留草稿；放弃的重复点击只提交一次，提交失败保留草稿供重试。
test("取消、重复放弃和失败恢复均保护本地草稿", async () => {
  const ref = createRef<TemplateWorkspaceHandle>();
  render(<TemplateWorkspace ref={ref} selection={{ environment: "local", templateId: null, name: "本地草稿", description: "" }} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  const save = mock(async () => {});
  const cancelled = await requestSwitch(ref, save);
  fireEvent.click(await screen.findByRole("button", { name: "取消" }));
  expect(String(await cancelled.result)).toContain("已取消路径切换");
  expect(save).not.toHaveBeenCalled();
  const deferred = Promise.withResolvers<void>();
  save.mockImplementation(() => deferred.promise);
  const failed = await requestSwitch(ref, save);
  const discard = await screen.findByRole("button", { name: "放弃修改" });
  act(() => { fireEvent.click(discard); fireEvent.click(discard); });
  expect(save).toHaveBeenCalledTimes(1);
  await act(async () => deferred.reject(new Error("设置锁被占用")));
  expect(String(await failed.result)).toContain("设置锁被占用");
  expect(screen.getByLabelText("模板名称").textContent).toBe("本地草稿");
  save.mockImplementation(async () => {});
  const retry = await requestSwitch(ref, save);
  fireEvent.click(await screen.findByRole("button", { name: "放弃修改" }));
  await act(async () => { await retry.result; });
  expect(screen.queryByLabelText("模板名称") === null).toBe(true);
});

// 场景：保存并切换先保存当前本地草稿，再提交配置；云端保存期间则可独立改本地路径。
test.each(["local", "cloud"] as const)("%s 保存与路径切换按各自环境协调", async environment => {
  const saved = savedTemplate();
  const order: string[] = [];
  const response = Promise.withResolvers<Response>();
  mockDesktop(async (_command, args) => {
    if (args.operation === "save") order.push("template");
    return { data: saved, warning: null };
  });
  fetchMock.mockImplementation((async (_url, options) => options?.method === "POST" ? response.promise : protobufTemplateResponse(saved, "get")) as typeof fetch);
  const ref = createRef<TemplateWorkspaceHandle>();
  render(<TemplateWorkspace ref={ref} selection={{ environment, templateId: saved.template_id }} onHome={() => {}} />);
  fireEvent.change(await screen.findByLabelText("示例文字"), { target: { value: "新草稿" } });
  if (environment === "cloud") {
    fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
  }
  const pending = await requestSwitch(ref, async () => { order.push("settings"); });
  if (environment === "local") fireEvent.click(await screen.findByRole("button", { name: "保存并切换" }));
  await act(async () => { await pending.result; });
  expect(order).toEqual(environment === "local" ? ["template", "settings"] : ["settings"]);
  if (environment === "cloud") {
    expect(screen.queryByText("正在保存…") !== null).toBe(true);
    await act(async () => response.resolve(protobufTemplateResponse(saved, "save")));
    expect(screen.getByLabelText("模板名称").textContent).toBe(saved.name);
  } else expect(screen.queryByLabelText("模板名称") === null).toBe(true);
});

// 场景：初次设置读取失败后仍可提交地址补丁；不提交未知模板路径或抹掉其他配置。
test("设置读取失败不阻止单独保存 API 地址", async () => {
  const invoke = mock(async (_command: string, args: Record<string, unknown>) => {
    if (!args?.id) throw new Error("暂时被锁定");
    return {};
  });
  mockDesktop(invoke);
  fetchMock.mockResolvedValue(Response.json([]));
  render(<PluginSettings />);
  await screen.findByRole("alert");
  fireEvent.change(screen.getByLabelText("后端服务地址"), { target: { value: "https://new.test" } });
  fireEvent.submit(screen.getByRole("form", { name: "通用设置" }));
  await screen.findByText(/已保存，新配置/);
  expect(invoke.mock.calls.find(([, args]) => args?.id)?.[1].values).toEqual({ api_url: "https://new.test" });
  expect(screen.getByLabelText<HTMLInputElement>("本地模板保存路径").disabled).toBe(true);
});

// 场景：设置保存失败反馈到表单；重试成功只刷新本地列表，不重挂主页或重读云端。
test("设置保存错误可重试，成功仅刷新本地列表", async () => {
  let localReads = 0;
  let rejectSave = true;
  const invoke = mock(async (command: string, args: Record<string, unknown>) => {
    if (command === "local_templates") { localReads++; return { data: [], warning: null }; }
    if (args?.id && rejectSave) throw new Error("目录不可写");
    return { $client: { api_url: "http://api.test:8000" } };
  });
  mockDesktop(invoke);
  fetchMock.mockImplementation((async url => String(url).includes("/api/settings/plugins") ? Response.json([]) : protobufListResponse([])) as typeof fetch);
  render(<HomePage />);
  await screen.findByText("暂无本地模板");
  const cloudReads = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/template")).length;
  const before = cloudReads();
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  const dialog = await screen.findByRole("dialog", { name: "设置" });
  await waitFor(() => expect(within(dialog).getByLabelText<HTMLInputElement>("本地模板保存路径").disabled).toBe(false));
  fireEvent.change(within(dialog).getByLabelText("本地模板保存路径"), { target: { value: "/tmp/TEMPLATES.JSON" } });
  fireEvent.submit(within(dialog).getByRole("form", { name: "通用设置" }));
  await within(dialog).findByText("目录不可写");
  expect(localReads).toBe(1);
  rejectSave = false;
  fireEvent.submit(within(dialog).getByRole("form", { name: "通用设置" }));
  await within(dialog).findByText(/已保存，新配置/);
  await waitFor(() => expect(localReads).toBe(2));
  expect(cloudReads()).toBe(before);
});

// 场景：宿主回退默认路径仍返回可用列表，同时展示不会自动消失的路径警告。
test("默认库回退警告可见且不阻断读取", async () => {
  mockDesktop(async () => ({ data: [], warning: "设置损坏，已回退默认模板库：/default/templates.json" }));
  const warning = spyOn(toast, "warning");
  expect(await listTemplates(undefined, "local")).toEqual([]);
  expect(warning).toHaveBeenCalledWith(expect.stringContaining("已回退默认模板库"), expect.objectContaining({ duration: Infinity }));
  toast.dismiss("local-template-storage");
});
