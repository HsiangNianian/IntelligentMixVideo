/** 动态配置表单、本地存储与切片请求联调；隔离 HTTP/桌面 IPC，执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { requestSegmentation } from "@/features/segmentation/api";
import { PluginSettings } from "@/features/settings/PluginSettings";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "@/features/settings/api";
import { apiBase, setApiBase } from "@/lib/api-base";
import { fetchMock, mockDesktop } from "./setup";
import { normalizeValues } from "@/features/settings/schema";

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

// 场景：桌面启动后设置目录与切片请求使用实际端口，不缓存模块加载时的地址。
test("设置和切片跟随内置后端运行时地址", async () => {
  const original = apiBase();
  const restore = mockDesktop(async () => ({}));
  try {
    setApiBase("http://127.0.0.1:43213/");
    fetchMock.mockResolvedValueOnce(Response.json([segmentation]));
    expect(await listPlugins()).toEqual([segmentation]);
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("http://127.0.0.1:43213/api/settings/plugins");
    fetchMock.mockResolvedValueOnce(Response.json({ segments: [] }));
    expect(await requestSegmentation({ script: "测试", asr_result: {} })).toEqual({ segments: [] });
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("http://127.0.0.1:43213/segmentations");
  } finally {
    setApiBase(original);
    restore();
  }
});

// 场景：真实表单保存后卸载重开恢复值，切片请求读取已保存快照且不改变本地配置。
test("保存切片设置并携带本地配置请求切片", async () => {
  let stored: Record<string, Values> = {};
  const restore = mockDesktop(async (command, args) => {
    expect(command).toBe("local_settings");
    if (args?.id) stored = { ...stored, [String(args.id)]: structuredClone(args.values as Values) };
    return structuredClone(stored);
  });
  try {
    fetchMock.mockResolvedValue(Response.json([segmentation]));
    const view = render(<PluginSettings />);
    await openModule("文案切片");
    await screen.findByRole("heading", { name: "文案切片" });
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://api.test:8000/api/settings/plugins");
    expect(screen.getByLabelText<HTMLInputElement>("请求超时（秒）").value).toBe("120");
    expect(screen.getByLabelText<HTMLInputElement>("API Key").type).toBe("password");
    fireEvent.change(screen.getByLabelText("模型 API 地址"), { target: { value: "https://client.test/v1" } });
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "client-test-key" } });
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "client-model" } });
    fireEvent.change(screen.getByLabelText("请求超时（秒）"), { target: { value: "8.5" } });
    fireEvent.submit(screen.getByRole("form", { name: "文案切片" }));
    await screen.findByText("已保存到当前客户端");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    view.unmount();
    fetchMock.mockResolvedValue(Response.json([segmentation]));
    render(<PluginSettings />);
    await openModule("文案切片");
    await screen.findByDisplayValue("client-model");
    const saved = structuredClone(stored);
    // 草稿修改不应提前进入请求使用的已保存配置。
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "unsaved-model" } });
    fetchMock.mockResolvedValueOnce(Response.json({ segments: [{ text: "甲乙" }] }));
    expect(await requestSegmentation({ script: "甲乙", asr_result: {} })).toEqual({ segments: [{ text: "甲乙" }] });
    const [url, options] = fetchMock.mock.calls.at(-1)!;
    expect(String(url)).toBe("http://api.test:8000/segmentations");
    expect(JSON.parse(String(options?.body))).toEqual({ script: "甲乙", asr_result: {}, config: saved.segmentation });
    expect(stored).toEqual(saved);
  } finally { restore(); }
});

// 场景：ASR 插入和拔出后重新打开设置反映目录变化，移除不删除本地配置；默认选中通用面板。
test("第二个模块按目录插拔且保留已保存值", async () => {
  const stored = { asr: { dashscope_api_key: "asr-test-key" } };
  const restore = mockDesktop(async () => structuredClone(stored));
  try {
    fetchMock.mockResolvedValueOnce(Response.json([segmentation, asr]));
    const first = render(<PluginSettings />);
    const navigation = await screen.findByRole("tablist", { name: "设置模块" });
    expect(navigation.getAttribute("aria-orientation")).toBe("vertical");
    expect(within(navigation).getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByRole("tab", { name: "通用" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("region", { name: "环境与连接" })).toBeTruthy();
    expect(screen.queryByRole("form")).toBeNull();
    await openModule("文案切片");
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "未保存模型" } });
    fireEvent.mouseDown(screen.getByRole("tab", { name: "语音识别" }), { button: 0 });
    await screen.findByRole("heading", { name: "语音识别" });
    expect(screen.queryByRole("form", { name: "文案切片" })).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("Dashscope Api Key").value).toBe("asr-test-key");
    expect(within(screen.getByRole("form", { name: "语音识别" })).getByRole("button", { name: "保存" })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("tab", { name: "语音识别" }), { key: "ArrowUp" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "文案切片" }).getAttribute("aria-selected")).toBe("true"));
    expect(within(screen.getByRole("form", { name: "文案切片" })).getByDisplayValue("未保存模型")).toBeTruthy();
    first.unmount();
    fetchMock.mockResolvedValueOnce(Response.json([segmentation]));
    const second = render(<PluginSettings />);
    await openModule("文案切片");
    await screen.findByRole("heading", { name: "文案切片" });
    expect(screen.queryByRole("heading", { name: "语音识别" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "语音识别" })).toBeNull();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(await readSettings()).toEqual(stored);
    second.unmount();
    fetchMock.mockResolvedValueOnce(Response.json([asr]));
    render(<PluginSettings />);
    await openModule("语音识别");
    await screen.findByDisplayValue("asr-test-key");
  } finally { restore(); }
});

// 场景：IPC 保存失败可见，界面不假报成功；不覆盖现有配置。
test("保存失败显示错误", async () => {
  const restore = mockDesktop(async (_command, args) => {
    if (args?.id) throw new Error("write failed");
    return { asr: { dashscope_api_key: "old-key" } };
  });
  try {
    fetchMock.mockResolvedValueOnce(Response.json([asr]));
    render(<PluginSettings />);
    await openModule("语音识别");
    await screen.findByDisplayValue("old-key");
    fireEvent.change(screen.getByLabelText("Dashscope Api Key"), { target: { value: "new-key" } });
    fireEvent.submit(screen.getByRole("form", { name: "语音识别" }));
    await screen.findByText("保存设置失败");
    expect(screen.queryByText("已保存到当前客户端")).toBeNull();
    expect(await readSettings()).toEqual({ asr: { dashscope_api_key: "old-key" } });
  } finally { restore(); }
});

// 场景：浏览器只保存内存副本，页面内重新读取可用，不写 localStorage。
test("浏览器配置仅保存在内存中", async () => {
  const values = { dashscope_api_key: "browser-test-key" };
  await saveSettings("browser-test", values);
  values.dashscope_api_key = "changed";
  const settings = await readSettings();
  expect(settings["browser-test"].dashscope_api_key).toBe("browser-test-key");
  settings["browser-test"].dashscope_api_key = "changed-again";
  expect((await readSettings())["browser-test"].dashscope_api_key).toBe("browser-test-key");
  expect(localStorage.length).toBe(0);
});

// 场景：未配置的旧客户端发送原请求；网络失败不重复提交。
test("无本地配置保持旧请求且失败不自动重试", async () => {
  const restore = mockDesktop(async () => ({}));
  try {
    fetchMock.mockResolvedValueOnce(Response.json({}, { status: 502 }));
    await expect(requestSegmentation({ script: "甲乙", asr_result: {} })).rejects.toThrow("502");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ script: "甲乙", asr_result: {} });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally { restore(); }
});

// 场景：空插件目录仍显示通用面板，接口失败有可见状态；卸载中断目录请求。
test("目录为空、读取失败及卸载清理", async () => {
  fetchMock.mockResolvedValueOnce(Response.json([]));
  const first = render(<PluginSettings />);
  const navigation = await screen.findByRole("tablist", { name: "设置模块" });
  expect(within(navigation).getAllByRole("tab")).toHaveLength(1);
  expect(screen.getByRole("tab", { name: "通用" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("region", { name: "环境与连接" })).toBeTruthy();
  first.unmount();
  fetchMock.mockResolvedValueOnce(Response.json({}, { status: 503 }));
  const second = render(<PluginSettings />);
  expect((await screen.findByRole("alert")).textContent).toContain("重新打开设置");
  second.unmount();
  fetchMock.mockImplementationOnce(((_url, options) => new Promise<Response>((_resolve, reject) => {
    options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  })) as typeof fetch);
  const third = render(<PluginSettings />);
  const signal = fetchMock.mock.calls.at(-1)![1]?.signal;
  third.unmount();
  await waitFor(() => expect(signal?.aborted).toBe(true));
});

// 场景：新增任意模块字段自动渲染，通用数值与字符串约束阻止保存且允许修正后提交。
test.each([
  [{ type: "number" }, "", "2", "必填"],
  [{ type: "integer" }, "1.5", "2", "整数"],
  [{ type: "number", exclusiveMinimum: 0 }, "0", "0.5", "必须大于"],
  [{ type: "number", exclusiveMaximum: 4 }, "4", "3.5", "必须小于"],
  [{ type: "number", minimum: 2 }, "1", "2", "不能小于"],
  [{ type: "number", maximum: 2 }, "3", "2", "不能大于"],
  [{ type: "string", pattern: "\\S" }, "   ", "有效", "格式"],
  [{ type: "string", minLength: 2 }, "甲", "甲乙", "至少"],
  [{ type: "string", maxLength: 2 }, "甲乙丙", "甲乙", "最多"],
] as const)("通用字段规则 %j", async (field, invalid, valid, error) => {
  const plugin: Plugin = { id: "new-module", name: "新模块", schema: {
    properties: { value: { ...field, title: "新增字段" } }, required: ["value"],
  } };
  let stored: Record<string, Values> = {};
  let writes = 0;
  const restore = mockDesktop(async (_command, args) => {
    if (args?.id) { writes++; stored[String(args.id)] = args.values as Values; }
    return structuredClone(stored);
  });
  try {
    fetchMock.mockResolvedValueOnce(Response.json([plugin]));
    render(<PluginSettings />);
    await openModule("新模块");
    const input = screen.getByLabelText<HTMLInputElement>("新增字段");
    fireEvent.change(input, { target: { value: invalid } });
    fireEvent.submit(screen.getByRole("form", { name: "新模块" }));
    expect((await screen.findByRole("alert")).textContent).toContain(error);
    expect(writes).toBe(0);
    expect(input.value).toBe(invalid);
    fireEvent.change(input, { target: { value: valid } });
    fireEvent.submit(screen.getByRole("form", { name: "新模块" }));
    await screen.findByText("已保存到当前客户端");
    expect(writes).toBe(1);
    expect(stored[plugin.id]).toEqual({ value: field.type === "string" ? valid : Number(valid) });
  } finally { restore(); }
});

// 场景：可选数字清空会从旧值中移除；必填布尔 false 可保存，general ID 不冲突也不显示孤立存储。
test("规范化空数字并保留 false，插件 ID 与通用导航隔离", async () => {
  const plugin: Plugin = { id: "general", name: "普通插件", schema: {
    properties: { optional: { type: "number", title: "可选数值", default: 60 }, enabled: { type: "boolean", title: "启用" } },
    required: ["enabled"],
  } };
  let stored: Record<string, Values> = { general: { optional: 8, enabled: false }, removed_module: { enabled: true } };
  const restore = mockDesktop(async (_command, args) => {
    if (args?.id) stored[String(args.id)] = args.values as Values;
    return structuredClone(stored);
  });
  try {
    fetchMock.mockResolvedValueOnce(Response.json([plugin]));
    const view = render(<PluginSettings />);
    await openModule("普通插件");
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "通用" }).getAttribute("aria-selected")).toBe("false");
    fireEvent.change(screen.getByLabelText("可选数值"), { target: { value: "" } });
    fireEvent.submit(screen.getByRole("form", { name: "普通插件" }));
    await screen.findByText("已保存到当前客户端");
    expect(stored).toEqual({ general: { enabled: false }, removed_module: { enabled: true } });
    view.unmount();
    fetchMock.mockResolvedValueOnce(Response.json([plugin]));
    render(<PluginSettings />);
    await openModule("普通插件");
    expect(screen.getByLabelText<HTMLInputElement>("可选数值").value).toBe("60");
  } finally { restore(); }
});

// 场景：不支持的字段与根结构必须显示错误而不是降级成普通输入，不妨碍其他模块表单。
test.each([
  { properties: { bad: { type: "object", properties: {} } } },
  { properties: { bad: { type: "array", items: { type: "string" } } } },
  { properties: { bad: { anyOf: [{ type: "number" }, { type: "null" }] } } },
  { properties: { bad: { type: "string", enum: ["one", "two"] } } },
  { properties: { bad: { $ref: "#/$defs/value" } } },
  { properties: { bad: { type: "string", pattern: "[" } } },
  { properties: { bad: { type: "number", multipleOf: 2 } } },
  { properties: { bad: { type: ["string", "null"] } } },
  { properties: null },
  { properties: {}, allOf: [] },
])("不支持的 Schema %j", async schema => {
  const bad = { id: "unsupported", name: "不支持模块", schema };
  fetchMock.mockResolvedValueOnce(Response.json([bad, asr]));
  render(<PluginSettings />);
  await openModule("不支持模块");
  expect((await screen.findByRole("alert")).textContent).toContain("不支持");
  expect(screen.queryByRole("form")).toBeNull();
  expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  await openModule("语音识别");
  expect(screen.getByRole("form", { name: "语音识别" })).toBeTruthy();
});

// 场景：非有限数字不依赖浏览器原生输入校验，保存规范化本身会拒绝。
test.each([NaN, Infinity, -Infinity])("拒绝非有限配置 %s", value => {
  expect(() => normalizeValues({ id: "number", name: "数字", schema: { properties: { value: { type: "number" } } } }, { value })).toThrow("有效数字");
});
