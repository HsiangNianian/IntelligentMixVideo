/** 动态配置表单、本地存储与切片请求联调；隔离 HTTP/桌面 IPC，执行 bun run test。 */
import { beforeEach, expect, mock, test } from "bun:test";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as remotion from "@/features/remotion_templates/api";
import { createComposition, getComposition } from "@/features/video_composition/api";
import { requestSegmentation } from "@/features/segmentation/api";
import { PluginSettings } from "@/features/settings/PluginSettings";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "@/features/settings/api";
import { fetchMock, mockDesktop } from "./setup";

// 现有插件用例验证 Debug 路径；普通模式用例显式覆盖，公共夹具逐例重置开关。
beforeEach(() => { process.env.IMV_DEBUG = "true"; });

// 场景：普通模式只请求客户端模块目录；旧切片值保留，但不进入业务请求。
test.each([undefined, "false", "TRUE"])("普通模式隔离模块配置：%s", async (mode) => {
  if (mode === undefined) delete process.env.IMV_DEBUG;
  else process.env.IMV_DEBUG = mode;
  const stored = { segmentation: { llm_model: "old-model", llm_api_key: "test-key" } };
  const invoke = mock(async () => structuredClone(stored));
  mockDesktop(invoke);
  fetchMock.mockResolvedValueOnce(Response.json([]));
  expect(await listPlugins()).toEqual([]);
  expect(fetchMock.mock.calls[0][0]).toBe("http://api.test:8000/api/settings/plugins?client_only=true");
  fetchMock.mockClear();
  fetchMock.mockResolvedValueOnce(Response.json({ segments: [] }));
  await requestSegmentation({ script: "测试", asr_result: {} });
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ script: "测试", asr_result: {} });
  expect(invoke).not.toHaveBeenCalled();
  expect(await readSettings()).toEqual(stored);
});

/** 与后端目录协议一致，字段由描述控制；API 的真实模型生成在 pytest 中覆盖。 */
const segmentation: Plugin = {
  id: "segmentation", name: "文案切片", schema: {
    properties: {
      llm_base_url: { type: "string", title: "模型 API 地址" },
      llm_api_key: { type: "string", title: "API Key", format: "password" },
      llm_model: { type: "string", title: "模型名称" },
      llm_timeout_seconds: { type: "number", title: "请求超时（秒）", default: 120, exclusiveMinimum: 0 },
      llm_max_retries: { type: "integer", title: "重试次数", default: 1, minimum: 0, maximum: 3 },
    }, required: ["llm_base_url", "llm_api_key", "llm_model"],
  },
};

/** 第二个模块只提供字段描述，不需要增加 React 组件。 */
const asr: Plugin = {
  id: "asr", name: "语音识别", schema: {
    properties: { dashscope_api_key: { type: "string", title: "Dashscope Api Key", format: "password", default: "" } },
  },
};

/** 默认选中通用面板；点击模块导航项后对应表单才进入可访问树。 */
async function openModule(name: string) {
  fireEvent.mouseDown(await screen.findByRole("tab", { name }), { button: 0 });
}

// 场景：非法值阻止写入，修正后保存；切片请求读取快照，但不发送启动时才应用的 HTTP 策略。
test("保存切片设置并携带本地配置请求切片", async () => {
  let stored: Record<string, Values> = {};
  const invoke = mock(async (_command: string, args: Record<string, unknown>) => {
    if (args?.id) stored = { ...stored, [String(args.id)]: structuredClone(args.values as Values) };
    return structuredClone(stored);
  });
  mockDesktop(invoke);
  fetchMock.mockResolvedValueOnce(Response.json([segmentation]));
  const view = render(<PluginSettings />);
  await openModule("文案切片");
  expect(screen.getByLabelText<HTMLInputElement>("API Key").type).toBe("password");
  fireEvent.change(screen.getByLabelText("模型 API 地址"), { target: { value: "https://client.test/v1" } });
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "client-test-key" } });
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "client-model" } });
  invoke.mockClear();
  fireEvent.change(screen.getByLabelText("请求超时（秒）"), { target: { value: "0" } });
  fireEvent.submit(screen.getByRole("form", { name: "文案切片" }));
  expect((await screen.findByRole("alert")).textContent).toContain("必须大于");
  expect(invoke).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("请求超时（秒）"), { target: { value: "8.5" } });
  fireEvent.submit(screen.getByRole("form", { name: "文案切片" }));
  await screen.findByText("已保存到当前客户端");
  view.unmount();
  fetchMock.mockResolvedValueOnce(Response.json([segmentation]));
  render(<PluginSettings />);
  await openModule("文案切片");
  await screen.findByDisplayValue("client-model");
  const saved = structuredClone(stored);
  stored.segmentation.allow_insecure_llm_http = false;
  // 草稿修改不应提前进入请求使用的已保存配置。
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "unsaved-model" } });
  fetchMock.mockResolvedValueOnce(Response.json({ segments: [{ text: "甲乙" }] }));
  expect(await requestSegmentation({ script: "甲乙", asr_result: {} })).toEqual({ segments: [{ text: "甲乙" }] });
  const [, options] = fetchMock.mock.calls.at(-1)!;
  expect(JSON.parse(String(options?.body))).toEqual({ script: "甲乙", asr_result: {}, config: saved.segmentation });
});

// 场景：切换模块保留各自草稿，未保存内容不写入本地配置。
test("切换模块保留草稿", async () => {
  const invoke = mock(async () => ({}));
  mockDesktop(invoke);
  fetchMock.mockResolvedValueOnce(Response.json([segmentation, asr]));
  render(<PluginSettings />);
  await openModule("文案切片");
  fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "未保存模型" } });
  await openModule("语音识别");
  await openModule("文案切片");
  expect(screen.getByDisplayValue("未保存模型")).toBeTruthy();
  expect(invoke.mock.calls).toHaveLength(1);
});

// 场景：IPC 保存失败可见，界面不假报成功，保留输入并允许修正。
test("保存失败显示错误", async () => {
  mockDesktop(async (_command, args) => {
    if (args?.id) throw new Error("write failed");
    return { asr: { dashscope_api_key: "old-key" } };
  });
  fetchMock.mockResolvedValueOnce(Response.json([asr]));
  render(<PluginSettings />);
  await openModule("语音识别");
  await screen.findByDisplayValue("old-key");
  fireEvent.change(screen.getByLabelText("Dashscope Api Key"), { target: { value: "new-key" } });
  fireEvent.submit(screen.getByRole("form", { name: "语音识别" }));
  await screen.findByText("保存设置失败");
  expect(screen.queryByText("已保存到当前客户端")).toBeNull();
  expect(screen.getByLabelText<HTMLInputElement>("Dashscope Api Key").value).toBe("new-key");
  fireEvent.change(screen.getByLabelText("Dashscope Api Key"), { target: { value: "corrected-key" } });
  expect(screen.queryByRole("alert")).toBeNull();
});

// 场景：接口失败仍可访问通用面板；卸载中断目录请求。
test("读取失败及卸载清理", async () => {
  fetchMock.mockResolvedValueOnce(Response.json({}, { status: 503 }));
  const second = render(<PluginSettings />);
  expect((await screen.findByRole("alert")).textContent).toContain("重新打开设置");
  expect(screen.getByRole("region", { name: "环境与连接" })).toBeTruthy();
  second.unmount();
  fetchMock.mockImplementationOnce(((_url, options) => new Promise<Response>((_resolve, reject) => {
    options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  })) as typeof fetch);
  const third = render(<PluginSettings />);
  const signal = fetchMock.mock.calls.at(-1)![1]?.signal;
  third.unmount();
  await waitFor(() => expect(signal?.aborted).toBe(true));
});

// 场景：清空可选数字、保留 false 及当前 Schema 未展示的 Debug 值；孤立存储不产生导航。
test("规范化空数字并保留 false，插件 ID 与通用导航隔离", async () => {
  const plugin: Plugin = { id: "general", name: "普通插件", schema: {
    properties: { optional: { type: "number", title: "可选数值", default: 60 }, enabled: { type: "boolean", title: "启用" } },
    required: ["enabled"],
  } };
  let stored: Record<string, Values> = { general: { optional: 8, enabled: false, debug_limit: 99 }, removed_module: { enabled: true } };
  mockDesktop(async (_command, args) => {
    if (args?.id) stored[String(args.id)] = args.values as Values;
    return structuredClone(stored);
  });
  fetchMock.mockResolvedValueOnce(Response.json([plugin]));
  render(<PluginSettings />);
  await openModule("普通插件");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(screen.getByRole("tab", { name: "通用" }).getAttribute("aria-selected")).toBe("false");
  fireEvent.change(screen.getByLabelText("可选数值"), { target: { value: "" } });
  fireEvent.submit(screen.getByRole("form", { name: "普通插件" }));
  await screen.findByText("已保存到当前客户端");
  expect(stored).toEqual({ general: { enabled: false, debug_limit: 99 }, removed_module: { enabled: true } });
});

// 场景：坏模块显示错误且没有保存入口，仍可切换到正常模块。
test("不支持的模块不影响其他模块表单", async () => {
  const bad = { id: "unsupported", name: "不支持模块", schema: { properties: { bad: { type: "object" } } } };
  fetchMock.mockResolvedValueOnce(Response.json([bad, asr]));
  render(<PluginSettings />);
  await openModule("不支持模块");
  expect((await screen.findByRole("alert")).textContent).toContain("不支持");
  expect(screen.queryByRole("form")).toBeNull();
  expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  await openModule("语音识别");
  expect(screen.getByRole("form", { name: "语音识别" })).toBeTruthy();
});

// 场景：两个模式的业务请求均读取最新凭据，凭据只进入请求头。
test.each(["false", "true"])("Agent 和 IMS 在模式 %s 下使用最新设置", async (mode) => {
  process.env.IMV_DEBUG = mode;
  let stored: Record<string, Values> = {};
  mockDesktop(async (_command, args) => {
    if (args.id) stored[String(args.id)] = structuredClone(args.values as Values);
    return structuredClone(stored);
  });
  await saveSettings("remotion_agent", { actor_api_key: "agent-private" });
  await saveSettings("ims", { ims_access_key_secret: "ims-private" });
  fetchMock.mockReset();
  for (let i = 0; i < 6; i++) fetchMock.mockResolvedValueOnce(Response.json({ models_configured: true }));
  await remotion.capabilities();
  await remotion.create("你好");
  await saveSettings("remotion_agent", { actor_api_key: "changed-private" });
  await remotion.message("work", { instruction: "修改" });
  await remotion.retry("job");
  await createComposition({ text: "合成" });
  await saveSettings("ims", { ims_access_key_secret: "new-ims-private" });
  await getComposition("task");
  const configs = fetchMock.mock.calls.map(([, options]) => {
    expect(String(options?.body)).not.toContain("private");
    const headers = new Headers(options?.headers);
    return JSON.parse(decodeURIComponent(headers.get("X-Remotion-Config") ?? headers.get("X-IMS-Config")!));
  });
  expect(configs).toEqual([
    { actor_api_key: "agent-private" }, { actor_api_key: "agent-private" },
    { actor_api_key: "changed-private" }, { actor_api_key: "changed-private" },
    { ims_access_key_secret: "ims-private" }, { ims_access_key_secret: "new-ims-private" },
  ]);
});

// 场景：创建接口的受理结果包在 data 中，helper 解包后直接返回任务 ID；查询响应保持扁平结构。
test("合成创建解包 data 包装的受理结果", async () => {
  fetchMock.mockResolvedValueOnce(Response.json({ code: 200, message: "操作成功", data: "task-a" }));
  expect(await createComposition({ text: "合成" })).toBe("task-a");
  fetchMock.mockResolvedValueOnce(Response.json({ taskId: "task-a", status: "succeeded" }));
  expect((await getComposition("task-a")).status).toBe("succeeded");
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "http://api.test:8000/api/v1/video-compositions",
    "http://api.test:8000/api/v1/video-compositions/task-a",
  ]);
});
