/** 工作区核心交互：真实表单、选择器和弹窗，只替换 SDK 预览及 HTTP；执行 bun run test。 */
import { expect, mock, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import type { Draft, EffectAsset } from "@/features/templates/model";
import { catalog, savedTemplate } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";
import { remotionServer } from "./remotion-server";
import { remotionJob } from "./remotion-fixtures";

// SDK 依赖视频、字体和硬件加速；本组只验证目录回传和最新草稿传给预览的行为。
mock.module("@/features/templates/TemplatePreview", () => ({
  /** 用轻量输出替代第三方播放器，保留工作区实际使用的输入与目录回调。 */
  TemplatePreview({ draft, onCatalog }: { draft: Draft; onCatalog: (items: EffectAsset[]) => void }) {
    useEffect(() => onCatalog(catalog), [onCatalog]);
    return <output aria-label="预览标题">{draft.editor.title}</output>;
  },
}));
const { TemplateWorkspace } = await import("@/features/templates/TemplateWorkspace");
const { default: HomePage } = await import("@/pages/HomePage");

// 回归：模板首页同时保留标题区时钟与可编辑工作区，避免替换页面时再次丢失时钟。
test("首页标题区显示时钟并保留模板工作区", async () => {
  remotionServer((path) => path === "/template" ? Response.json([]) : undefined);
  render(<HomePage />);
  expect(screen.getByRole("tab", {name: "主页"}).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByRole("region", {name: "字效聊天"})).toBeNull();
  fireEvent.mouseDown(screen.getByRole('tab', {name: '模板库'}), {button: 0});
  await screen.findByText("共享模板库 · 0 个模板");
  const clock = screen.getByRole("region", { name: "当前时间" });
  expect(clock.closest("header")).not.toBeNull();
  expect(within(clock).getByRole("time").getAttribute("datetime")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 1, name: "特效模板" })).toBeTruthy();
  expect(screen.getByLabelText("模板名称")).toBeTruthy();
});

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

// 回归：初次列表请求未完成时仍可编辑和新建，失败及刷新都不丢失本地草稿。
test("模板 API 等待或失败时仍能编辑预览并保护新建草稿", async () => {
  let rejectList!: (error: Error) => void;
  fetchMock.mockReturnValueOnce(new Promise<Response>((_resolve, reject) => {
    rejectList = reject;
  }));
  render(<TemplateWorkspace />);
  const name = screen.getByLabelText<HTMLInputElement>("模板名称");
  // Happy DOM 不会把 fieldset 的禁用状态计入 input.disabled，直接检查原生禁用容器。
  expect(name.closest("fieldset")?.disabled).toBe(false);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "新建模板" }).disabled).toBe(false);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存模板" }).disabled).toBe(true);
  fireEvent.change(name, { target: { value: "本地草稿" } });
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "等待 API 时编辑" } });
  expect(screen.getByLabelText("预览标题").textContent).toBe("等待 API 时编辑");
  // 提交表单也不能绕过加载保护；新建弹窗的保存入口遵守相同限制。
  fireEvent.submit(name.closest("form")!);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "新建模板" }));
  const dialog = await screen.findByRole("dialog");
  const saveAndSwitch = within(dialog).getByRole<HTMLButtonElement>("button", { name: "保存并切换" });
  expect(saveAndSwitch.disabled).toBe(true);
  fireEvent.click(saveAndSwitch);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(name.value).toBe("本地草稿");
  fireEvent.click(screen.getByRole("button", { name: "新建模板" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(name.value).toBe("");
  expect(screen.queryByText(/有未保存的修改/)).toBeNull();
  fireEvent.change(name, { target: { value: "重新编辑" } });
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "请求失败也保留" } });
  await act(async () => rejectList(new TypeError("Failed to fetch")));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toBe("无法连接服务端，请确认 API 已启动后重试。可使用桌面客户端切换到本地环境。");
  expect(name.value).toBe("重新编辑");
  expect(screen.getByLabelText("预览标题").textContent).toBe("请求失败也保留");
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "离线继续编辑" } });
  fetchMock.mockResolvedValueOnce(Response.json([savedTemplate()]));
  fireEvent.click(screen.getByRole("button", { name: "刷新列表" }));
  await screen.findByText("模板列表已刷新，当前编辑内容已保留");
  expect(name.value).toBe("重新编辑");
  expect(screen.getByLabelText("预览标题").textContent).toBe("离线继续编辑");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存模板" }).disabled).toBe(false);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

// 回归：允许初始请求期间编辑后，晚到的远端列表只更新列表，不覆盖草稿或清除脏状态。
test("初始模板列表晚到时保留已编辑的本地草稿", async () => {
  let resolveList!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => {
    resolveList = resolve;
  }));
  render(<TemplateWorkspace />);
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "等待中创建" } });
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "已经开始预览" } });
  await act(async () => resolveList(Response.json([savedTemplate()])));
  await screen.findByText("共享模板库 · 1 个模板 · 有未保存的修改");
  expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("等待中创建");
  expect(screen.getByLabelText("预览标题").textContent).toBe("已经开始预览");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存模板" }).disabled).toBe(false);
});

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
  await within(dialog).findByText(/数据库暂不可用/);
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

// 测试取消动画或清除本页效果时修复无效时长，避免禁用输入留下 null、阻止保存。
test.each([
  ["无效果", "", 0.5],
  ["清除本页效果", "", 0.5],
  ["无效果", "0", 0.5],
  ["清除本页效果", "4", 0.5],
  ["无效果", "2", 2],
  ["清除本页效果", "2", 2],
] as const)("取消动画后修复无效时长并保留有效设置：%s / %s", async (operation, duration, expected) => {
  const saved = savedTemplate();
  saved.editor.subtitleIn = "in/fade_in";
  fetchMock.mockResolvedValueOnce(Response.json([saved]));
  render(<TemplateWorkspace />);
  await screen.findByText("共享模板库 · 1 个模板");
  fetchMock.mockResolvedValueOnce(Response.json(saved));
  await choose("打开模板", saved.name);
  await screen.findByDisplayValue(saved.name);
  fireEvent.change(screen.getAllByLabelText("时长 / 秒")[0], { target: { value: duration } });
  if (operation === "无效果") await choose("入场动画", "无效果");
  else fireEvent.click(screen.getByRole("button", { name: operation }));
  fetchMock.mockResolvedValueOnce(Response.json(saved));
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText(`模板「${saved.name}」已保存`);
  const body = JSON.parse(String(fetchMock.mock.calls[2][1]?.body));
  expect(body.editor.titleIn).toBe("");
  expect(body.editor.titleInDuration).toBe(expected);
  expect(body.editor.subtitleIn).toBe("in/fade_in");
});

// 顶层功能页签切换不能清空聊天或取消后台任务，返回后应继续读取同一个 job。
test.each(["云端", "本地"])("切换%s模板库后保留字效会话并继续接收 SSE", async (environment) => {
  const invoke = mock(async (_command: string): Promise<unknown> => []);
  const restoreDesktop = mockDesktop(invoke);
  try {
    const fake = remotionServer((path) => {
      if (path === "/template") return Response.json([]);
      if (path === "/works") return Response.json({work: {id: "work-1"}, job: remotionJob("running")});
    });
    render(<HomePage />);
    fireEvent.mouseDown(screen.getByRole("tab", {name: "Remotion 字效"}), {button: 0});
    fireEvent.change(screen.getByLabelText("字效描述"), {target: {value: "保留这个任务"}});
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    await screen.findByRole("button", {name: "停止"});
    fireEvent.change(screen.getByLabelText("字效描述"), {target: {value: "未发送草稿"}});
    fireEvent.mouseDown(screen.getByRole("tab", {name: "模板库"}), {button: 0});
    expect(screen.getByRole("tab", {name: "模板库"}).getAttribute("aria-selected")).toBe("true");
    await screen.findByText("共享模板库 · 0 个模板");
    if (environment === "本地") {
      await choose("当前环境", "本地");
      await screen.findByText("本地模板库 · 0 个模板");
      expect(invoke.mock.calls.at(-1)?.[0]).toBe("local_templates");
    }
    // 隐藏期间通过原订阅完成同一任务；切回不重新创建或重复建立连接。
    await waitFor(() => expect(fake.streams.size).toBe(1));
    await act(async () => fake.advance(remotionJob()));
    await waitFor(()=>expect(screen.getByLabelText("成功版本 V1")).toBeTruthy(), {timeout:2500});
    fireEvent.mouseDown(screen.getByRole("tab", {name: "Remotion 字效"}), {button: 0});
    expect(within(screen.getByRole("log")).queryByText("保留这个任务") !== null).toBe(true);
    expect(screen.getByLabelText<HTMLTextAreaElement>("字效描述").value).toBe("未发送草稿");
    expect(fetchMock.mock.calls.filter(call=>String(call[0]).endsWith("/cancel"))).toHaveLength(0);
    await waitFor(()=>expect(screen.getByRole("button",{name:"复制代码"}).hasAttribute("disabled")).toBe(false), {timeout:2500});
    expect(fetchMock.mock.calls.filter(call=>String(call[0]).endsWith("/works"))).toHaveLength(1);
    const frame = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
    const source = frame.src;
    fireEvent.change(screen.getByLabelText("背景视频直链"), {target:{value:"https://media.test/background.mp4"}});
    fireEvent.mouseDown(screen.getByRole("tab",{name:"模板库"}),{button:0});
    fireEvent.mouseDown(screen.getByRole("tab",{name:"Remotion 字效"}),{button:0});
    expect(screen.getByTitle("Remotion 字效播放器")).toBe(frame);
    expect(frame.src).toBe(source);
    expect(screen.getByLabelText<HTMLInputElement>("背景视频直链").value).toBe("https://media.test/background.mp4");
    expect(within(screen.getByRole("tabpanel", {name: "Remotion 字效"})).getByLabelText<HTMLInputElement>("字号").value).toBe("64");
  } finally {
    restoreDesktop();
  }
});

// 测试桌面默认云端，主动切换本地后使用 IPC；未保存切换保护不变，两个库不混合。
test("本地保存并切换云端，取消时保留草稿", async () => {
  const saved = savedTemplate();
  const invoke = mock(async (_command: string, args: Record<string, unknown>): Promise<unknown> => {
    if (args.operation === "list") return [saved];
    return { ...saved, ...args.draft as object };
  });
  const restoreDesktop = mockDesktop(invoke);
  try {
    fetchMock.mockResolvedValueOnce(Response.json([]));
    render(<TemplateWorkspace />);
    await screen.findByText("共享模板库 · 0 个模板");
    expect(screen.getByLabelText("当前环境").textContent).toBe("云端");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
    await choose("当前环境", "本地");
    await screen.findByText("本地模板库 · 1 个模板");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await choose("打开模板", saved.name);
    await screen.findByDisplayValue(saved.name);
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "本地修改" } });
    await choose("当前环境", "云端");
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "取消" }));
    expect(screen.getByDisplayValue("本地修改")).toBeTruthy();
    await choose("当前环境", "云端");
    invoke.mockRejectedValueOnce("磁盘不可写");
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "保存并切换" }));
    await within(await screen.findByRole("dialog")).findByText("磁盘不可写");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue("本地修改")).toBeTruthy();
    fetchMock.mockResolvedValueOnce(Response.json([]));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "保存并切换" }));
    await screen.findByText("共享模板库 · 0 个模板");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
    expect(invoke.mock.calls.at(-1)?.[1]).toMatchObject({ operation: "save", id: saved.template_id, draft: { name: "本地修改" } });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  } finally {
    restoreDesktop();
  }
});

// 测试启动时断网或服务不可用仍留在云端，提示后由用户切换本地并清除错误。
test.each(["network", "503"])("云端不可用时提示手动切换本地：%s", async (failure) => {
  const invoke = mock(async (): Promise<unknown> => []);
  const restoreDesktop = mockDesktop(invoke);
  try {
    if (failure === "network") fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    else fetchMock.mockResolvedValueOnce(Response.json({ detail: "数据库暂不可用" }, { status: 503 }));
    render(<TemplateWorkspace />);
    expect((await screen.findByRole("alert")).textContent).toContain("可在「当前环境」中切换到本地环境");
    expect(screen.getByLabelText("当前环境").textContent).toBe("云端");
    expect(invoke).not.toHaveBeenCalled();
    await choose("当前环境", "本地");
    await screen.findByText("本地模板库 · 0 个模板");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally {
    restoreDesktop();
  }
});

// 测试目标库加载失败时不更换环境或草稿，重试放弃修改成功后只展示目标库。
test("切换环境读取失败后可重试", async () => {
  await openExistingTemplate();
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "云端草稿" } });
  const invoke = mock(async (): Promise<unknown> => []);
  const restoreDesktop = mockDesktop(invoke);
  try {
    await choose("当前环境", "本地");
    invoke.mockRejectedValueOnce("文件读取失败");
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
    await within(await screen.findByRole("dialog")).findByText("文件读取失败");
    expect(screen.getByLabelText("当前环境").textContent).toBe("云端");
    expect(screen.getByDisplayValue("云端草稿")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "放弃修改" }));
    await screen.findByText("本地模板库 · 0 个模板");
    expect(screen.getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  } finally {
    restoreDesktop();
  }
});

// 本地与云端草稿切到字效再返回仍保留，原有新建保护继续生效；离开首页才卸载。
test.each(["云端", "本地"])("页签切换保留%s模板草稿及未保存保护", async (environment) => {
  const restore = mockDesktop(async () => []);
  try {
    remotionServer((path) => path === "/template" ? Response.json([]) : undefined);
    const view = render(<HomePage />);
    expect(screen.queryByLabelText("模板名称")).toBeNull();
    fireEvent.mouseDown(screen.getByRole("tab", {name: "模板库"}), {button: 0});
    await screen.findByText("共享模板库 · 0 个模板");
    if (environment === "本地") {
      await choose("当前环境", "本地");
      await screen.findByText("本地模板库 · 0 个模板");
    }
    const name = screen.getByLabelText<HTMLInputElement>("模板名称");
    fireEvent.change(name, {target: {value: "保留草稿名称"}});
    fireEvent.change(screen.getByLabelText("示例文字"), {target: {value: "保留示例文字"}});
    const reads = fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/template")).length;
    fireEvent.mouseDown(screen.getByRole("tab", {name: "Remotion 字效"}), {button: 0});
    expect(screen.queryByRole("textbox", {name: "模板名称"})).toBeNull();
    fireEvent.mouseDown(screen.getByRole("tab", {name: "模板库"}), {button: 0});
    expect(screen.getByLabelText("模板名称") === name).toBe(true);
    expect(name.value).toBe("保留草稿名称");
    expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留示例文字");
    expect(screen.getByLabelText("当前环境").textContent).toBe(environment);
    expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/template"))).toHaveLength(reads);
    fireEvent.click(screen.getByRole("button", {name: "新建模板"}));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", {name: "取消"}));
    expect(name.value).toBe("保留草稿名称");
    view.unmount();
    expect(name.isConnected).toBe(false);
  } finally {
    restore();
  }
});

// 场景：设置弹窗关闭丢弃设置草稿，后台聊天草稿保留，模板编辑器没有提前加载。
test("设置弹窗保留工作区并丢弃未保存设置", async () => {
  process.env.IMV_DEBUG = "true";
  remotionServer(path => {
    if (path === "/template") return Response.json([]);
    if (path === "/api/settings/plugins") return Response.json([
      { id: "segmentation", name: "文案切片", schema: { properties: { model: { type: "string", title: "切片模型" } } } },
    ]);
  });
  render(<HomePage />);
  fireEvent.mouseDown(screen.getByRole("tab", {name: "Remotion 字效"}), {button: 0});
  fireEvent.change(screen.getByLabelText("字效描述"), { target: { value: "保留聊天草稿" } });
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  const dialog = await screen.findByRole("dialog", { name: "设置" });
  fireEvent.mouseDown(await within(dialog).findByRole("tab", { name: "文案切片" }), { button: 0 });
  fireEvent.change(within(dialog).getByLabelText("切片模型"), { target: { value: "未保存草稿" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText<HTMLTextAreaElement>("字效描述").value).toBe("保留聊天草稿");
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  const reopened = await screen.findByRole("dialog", { name: "设置" });
  fireEvent.mouseDown(await within(reopened).findByRole("tab", { name: "文案切片" }), { button: 0 });
  expect(within(reopened).getByLabelText<HTMLInputElement>("切片模型").value).toBe("");
  expect(screen.queryByLabelText("模板名称")).toBeNull();
});

// 场景：桌面主页按云端、本地顺序展示两个库，同 ID 模板根据选择的环境读取最新详情。
test.each(["cloud", "local"] as const)("主页选择 %s 模板进入对应编辑环境", async (environment) => {
  const cloud = { ...savedTemplate(), name: "云端款式" };
  const local = { ...savedTemplate(), name: "本地款式" };
  const restore = mockDesktop(async (_command, args) => args.operation === "list" ? [local] : local);
  try {
    remotionServer((path) => Response.json(path === "/template" ? [cloud] : cloud));
    render(<HomePage />);
    const cloudSection = screen.getByRole("region", { name: "云端模板" });
    const localSection = screen.getByRole("region", { name: "本地模板" });
    expect(cloudSection.compareDocumentPosition(localSection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await within(cloudSection).findByRole("button", { name: "选择模板：云端款式" });
    await within(localSection).findByRole("button", { name: "选择模板：本地款式" });
    const selected = environment === "cloud" ? cloud : local;
    fireEvent.click(screen.getByRole("button", { name: `选择模板：${selected.name}` }));
    await screen.findByDisplayValue(selected.name);
    expect(screen.getByRole("tab", { name: "模板库" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("当前环境").textContent).toBe(environment === "cloud" ? "云端" : "本地");
    expect(screen.getByLabelText("预览标题").textContent).toBe(selected.editor.title);
    expect(screen.queryByText(/有未保存的修改/)).toBeNull();
  } finally {
    restore();
  }
});

// 场景：云端失败不影响本地选择，重试只重新读取云端；返回主页重新读取保存后的名称。
test("主页分别处理失败、重试和返回后的列表更新", async () => {
  const local = { ...savedTemplate(), name: "本地可用" };
  const restore = mockDesktop(async (_command, args) => args.operation === "list" ? [local] : local);
  try {
    fetchMock.mockResolvedValueOnce(Response.json({ detail: "云端维护中" }, { status: 503 }));
    render(<HomePage />);
    const cloudSection = screen.getByRole("region", { name: "云端模板" });
    await within(cloudSection).findByRole("alert");
    expect(await screen.findByRole("button", { name: "选择模板：本地可用" })).toBeTruthy();
    fetchMock.mockResolvedValueOnce(Response.json([]));
    fireEvent.click(within(cloudSection).getByRole("button", { name: "重试" }));
    await within(cloudSection).findByText("暂无云端模板，可以前往模板库创建。");
    fireEvent.click(screen.getByRole("button", { name: "选择模板：本地可用" }));
    await screen.findByDisplayValue("本地可用");
    local.name = "重新命名";
    fetchMock.mockResolvedValueOnce(Response.json([]));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
    await screen.findByRole("button", { name: "选择模板：重新命名" });
    expect(screen.queryByRole("button", { name: "选择模板：本地可用" })).toBeNull();
  } finally {
    restore();
  }
});

// 场景：浏览器明确提示本地能力限制；云端空列表正常显示，退出主页中止未完成的请求。
test("浏览器主页展示本地限制并清理列表请求", async () => {
  fetchMock.mockResolvedValueOnce(Response.json([]));
  render(<HomePage />);
  await screen.findByText("暂无云端模板，可以前往模板库创建。");
  expect(screen.getByText("请在桌面客户端中查看和选择本地模板。")).toBeTruthy();
  let finishRead!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => { finishRead = resolve; }));
  fireEvent.click(within(screen.getByRole("region", { name: "云端模板" })).getByRole("button", { name: "刷新" }));
  const signal = fetchMock.mock.calls.at(-1)?.[1]?.signal;
  expect(signal?.aborted).toBe(false);
  fetchMock.mockResolvedValueOnce(Response.json([]));
  fireEvent.mouseDown(screen.getByRole("tab", { name: "模板库" }), { button: 0 });
  await screen.findByText("共享模板库 · 0 个模板");
  expect(signal?.aborted).toBe(true);
  await act(async () => finishRead(Response.json([savedTemplate()])));
  expect(screen.queryByRole("button", { name: "选择模板：已有模板" })).toBeNull();
});

// 场景：从主页选择另一模板，取消保留草稿；再次选择可放弃修改，重新选择当前模板不清除草稿。
test("主页选择复用未保存保护且每次点击只处理一次", async () => {
  const first = savedTemplate();
  const second = { ...savedTemplate(), template_id: "second", name: "另一模板" };
  remotionServer((path) => {
    return Response.json(path === "/template" ? [first, second] : path.endsWith("second") ? second : first);
  });
  render(<HomePage />);
  fireEvent.click(await screen.findByRole("button", { name: `选择模板：${first.name}` }));
  await screen.findByDisplayValue(first.name);
  fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "未保存草稿" } });
  fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
  fireEvent.click(await screen.findByRole("button", { name: `选择模板：${first.name}` }));
  expect(screen.getByDisplayValue("未保存草稿")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
  fireEvent.click(await screen.findByRole("button", { name: "选择模板：另一模板" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "取消" }));
  expect(screen.getByDisplayValue("未保存草稿")).toBeTruthy();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
  fireEvent.click(await screen.findByRole("button", { name: "选择模板：另一模板" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
  await screen.findByDisplayValue(second.name);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText(/有未保存的修改/)).toBeNull();
});

// 场景：跨库选择读取详情失败，原环境和草稿保留；重试时先保存到原库，成功后才切换。
test("主页跨库选择失败保留原环境，保存并切换写入原库", async () => {
  const cloud = { ...savedTemplate(), name: "云端模板名称" };
  const local = { ...savedTemplate(), name: "本地模板名称" };
  let unavailable = true;
  const restore = mockDesktop(async (_command, args) => {
    if (args.operation === "list") return [local];
    if (unavailable) throw new Error("本地模板读取失败");
    return local;
  });
  try {
    remotionServer((path, options) => {
      if (options?.method === "POST") return Response.json({ ...cloud, ...JSON.parse(String(options.body)) });
      return Response.json(path === "/template" ? [cloud] : cloud);
    });
    render(<HomePage />);
    fireEvent.click(await screen.findByRole("button", { name: `选择模板：${cloud.name}` }));
    await screen.findByDisplayValue(cloud.name);
    fireEvent.change(screen.getByLabelText("模板名称"), { target: { value: "云端修改" } });
    fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
    fireEvent.click(await screen.findByRole("button", { name: `选择模板：${local.name}` }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
    await within(screen.getByRole("dialog")).findByText("本地模板读取失败");
    expect(screen.getByLabelText("当前环境").textContent).toBe("云端");
    expect(screen.getByDisplayValue("云端修改")).toBeTruthy();
    unavailable = false;
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "保存并切换" }));
    await screen.findByDisplayValue(local.name);
    expect(screen.getByLabelText("当前环境").textContent).toBe("本地");
    const writes = fetchMock.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ name: "云端修改", template_id: cloud.template_id });
    expect(screen.queryByRole("dialog")).toBeNull();
  } finally {
    restore();
  }
});
