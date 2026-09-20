/** 工作区核心测试：使用真实 React 控件和随包目录检查创建、独立对象与草稿保护；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import HomePage from "@/pages/HomePage";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { TemplateCollection, type TemplateSelection } from "@/features/templates/TemplateHome";
import { savedTemplate } from "./fixtures";

/** 生成独立的主页新建输入，不包含服务端响应或存储数据。 */
function creation(name = "旅行模板", environment: "cloud" | "local" = "cloud"): TemplateSelection {
  return { templateId: null, environment, name, description: "适用于旅行视频" };
}

/** 从左侧真实资产创建文字对象，供后续参数编辑场景使用。 */
async function addText(label = "顶部标题") {
  const assets = await screen.findByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("button", { name: "花字" }));
  fireEvent.keyDown(within(assets).getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: label }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用花字：/ })[0]);
}

// 场景：云端和本地新模板均为空；选择作用对象不会添加，点击资产才创建独立对象。
test.each(["cloud", "local"] as const)("%s 新模板通过左侧资产创建画面对象", async (environment) => {
  render(<TemplateWorkspace selection={creation("空白模板", environment)} onHome={() => {}} />);
  const applied = await screen.findByRole("region", { name: "已添加特效" });
  expect(within(applied).queryAllByRole("button")).toHaveLength(0);
  expect(within(applied).getByText("从左侧特效资产选择并添加效果。")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "特效设置" })).toBeNull();
  expect(screen.queryByLabelText("轨道时间设置")).toBeNull();
  fireEvent.keyDown(screen.getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: "底部字幕" }));
  expect(within(applied).queryAllByRole("button")).toHaveLength(0);
  await addText("底部字幕");
  expect(within(applied).getAllByRole("button")).toHaveLength(1);
  expect(within(applied).getByRole("button", { name: "编辑底部字幕", pressed: true })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "手动添加的字幕" } });
  await addText();
  expect(within(applied).getAllByRole("button")).toHaveLength(2);
  fireEvent.click(within(applied).getByRole("button", { name: "编辑底部字幕" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("手动添加的字幕");
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.click(within(applied).getByRole("button", { name: "编辑顶部标题" }));
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  expect(within(applied).queryAllByRole("button")).toHaveLength(0);
});

// 场景：时间输入尚未完整时停止保存；修正后使用最新草稿继续执行模板校验。
test("空白时间阻止保存，修正后恢复保存流程", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  const input = await screen.findByLabelText("开始时间 / 秒");
  fireEvent.change(input, { target: { value: "" } });
  const error = "开始时间须为非负秒数，或 0% 至小于 100% 的百分比";
  expect(screen.getByText(error)).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  expect(screen.getByText(error)).toBeTruthy();
  expect(screen.queryByText("请至少选择一个效果")).toBeNull();
  fireEvent.change(input, { target: { value: "2" } });
  expect(screen.queryByText(error)).toBeNull();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("请至少选择一个效果");
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("2");
});

// 场景：同类特效各有独立参数面板，时间修改、非法范围与删除均保持其他实例。
test("重复添加特效后分别设置时间并删除指定实例", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await screen.findByRole("region", { name: "特效资产" });
  const assets = screen.getByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("button", { name: "画面特效" }));
  const asset = within(assets).getAllByRole("button", { name: /^应用画面特效：/ })[0];
  fireEvent.click(asset);
  fireEvent.change(screen.getByLabelText("开始时间 / 秒"), { target: { value: "2" } });
  fireEvent.keyDown(screen.getByRole("combobox", { name: "持续方式" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: "固定时长" }));
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "3" } });
  fireEvent.click(asset);
  expect(screen.getByRole("button", { name: "编辑画面特效 2", pressed: true })).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "编辑画面特效 1" }));
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("2");
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "0" } });
  expect(screen.getByText("持续时间须大于零")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑画面特效" }));
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("0");
  expect(screen.getByRole("combobox", { name: "持续方式" }).textContent).toBe("持续到视频结束");
});

// 场景：设置关闭后继续保护未保存草稿，取消和保存校验失败均保留关闭状态，放弃后才进入新模板。
test("设置关闭时仍保护模板切换，放弃后恢复新模板的编辑状态", async () => {
  const view = render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "仍需保护的标题" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  view.rerender(<TemplateWorkspace selection={creation("另一模板")} onHome={() => {}} />);
  let dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("region", { name: "特效设置" })).toBeNull();
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("仍需保护的标题");
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  view.rerender(<TemplateWorkspace selection={creation("另一模板")} onHome={() => {}} />);
  dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "保存并切换" }));
  await within(dialog).findByText("请至少选择一个效果");
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(screen.queryByLabelText("示例文字")).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("模板名称").textContent).toBe("另一模板");
  expect(screen.queryByLabelText("示例文字")).toBeNull();
  expect(within(screen.getByRole("region", { name: "已添加特效" })).queryAllByRole("button")).toHaveLength(0);
});

// 场景：关闭字幕设置后应用文字资产仍使用最近的文字对象，重新打开时保留字幕参数和标题内容。
test("关闭设置后应用资产沿用最近选择的字幕对象", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  await addText("底部字幕");
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "保留字幕内容" } });
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "59" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  fireEvent.click(screen.getAllByRole("button", { name: /^应用花字：/ })[0]);
  expect(screen.getByRole("button", { name: "编辑底部字幕", pressed: true })).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留字幕内容");
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("59");
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("让每一帧 都有风格");
  expect(screen.getByRole("combobox", { name: "花字样式" }).textContent).toBe("无效果");
});

// 场景：未保存保护在设置关闭后仍然生效，卸载工作区会移除页面退出监听。
test("关闭设置保留页面退出保护，卸载清理监听", async () => {
  const view = render(<TemplateWorkspace onHome={() => {}} />);
  expect(fireEvent(window, new Event("beforeunload", { cancelable: true }))).toBe(true);
  view.rerender(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  expect(fireEvent(window, new Event("beforeunload", { cancelable: true }))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  expect(fireEvent(window, new Event("beforeunload", { cancelable: true }))).toBe(false);
  view.unmount();
  expect(fireEvent(window, new Event("beforeunload", { cancelable: true }))).toBe(true);
});

/** 填写主页创建表单，界面自行跳转到模板库。 */
async function createFromHome(name = "旅行模板") {
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  const dialog = await screen.findByRole("dialog", { name: "新建云端模板" });
  fireEvent.change(within(dialog).getByLabelText("模板名称"), { target: { value: name } });
  fireEvent.change(within(dialog).getByLabelText("模板描述"), { target: { value: "  适用于旅行视频  " } });
  fireEvent.submit(within(dialog).getByRole("button", { name: "进入编辑" }).closest("form")!);
  return screen.findByLabelText("模板信息");
}

// 场景：直接进入模板库显示主页入口，时钟和默认主页保持可用。
test("未选择模板时通过主页开始创作", async () => {
  render(<HomePage />);
  expect(screen.getByRole("tab", { name: "主页" }).getAttribute("aria-selected")).toBe("true");
  expect(within(screen.getByRole("region", { name: "当前时间" })).getByRole("time").getAttribute("datetime")).toBeTruthy();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "模版编辑" }), { button: 0 });
  expect(screen.getByText("请从主页选择已有模板或创建新模板。")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "保存模板" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "前往主页" }));
  expect(screen.getByRole("tab", { name: "主页" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByText("请在桌面客户端中查看和选择本地模板。")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "新建本地模板" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "新建云端模板" }).disabled).toBe(false);
});

// 场景：主页填写信息后跳转，模板库只读展示并明确新模板尚未保存。
test("主页创建后显示环境、名称与描述，模板库只保留保存入口", async () => {
  render(<HomePage />);
  const info = await createFromHome("  旅行模板  ");
  expect(screen.getByRole("tab", { name: "模版编辑" }).getAttribute("aria-selected")).toBe("true");
  expect(within(info).getByLabelText("当前环境").textContent).toBe("云端");
  expect(within(info).getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(within(info).getByLabelText("模板描述").textContent).toBe("适用于旅行视频");
  expect(within(info).queryByRole("textbox")).toBeNull();
  expect(within(info).queryByRole("combobox")).toBeNull();
  expect(within(info).getAllByRole("button").map((button) => button.textContent)).toEqual(["保存模板"]);
  expect(screen.queryByRole("button", { name: "新建云端模板" })).toBeNull();
  expect(screen.queryByRole("button", { name: "刷新列表" })).toBeNull();
  expect(screen.getByText("新模板 · 尚未保存")).toBeTruthy();
});

// 场景：纯空白名称不能创建，取消对话框不切换页面，重新打开清空未提交信息。
test("创建表单拒绝空白名称并支持取消", async () => {
  render(<HomePage />);
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  let dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("模板名称"), { target: { value: "   " } });
  fireEvent.submit(within(dialog).getByRole("button", { name: "进入编辑" }).closest("form")!);
  expect(within(dialog).getByRole("alert").textContent).toBe("请输入模板名称");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByRole("tab", { name: "主页" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText<HTMLInputElement>("模板名称").value).toBe("");
  expect(within(dialog).getByLabelText<HTMLTextAreaElement>("模板描述").maxLength).toBe(1000);
  expect(within(dialog).getByLabelText<HTMLInputElement>("模板名称").maxLength).toBe(100);
});

// 场景：每行名称与说明共同触发模板选择，云端和本地传递各自环境及模板 ID。
test.each(["cloud", "local"] as const)("%s 模板整行选择保留来源与 ID", (environment) => {
  const template = { ...savedTemplate(), description: "用于产品视频的文字效果" };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection environment={environment}
    collection={{ templates: [template], loading: false, error: "", unavailable: false, refresh: () => {} }}
    onSelect={(selection) => selections.push(selection)} />);
  const row = screen.getByRole("button", { name: `选择模板：${template.name}` });
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
  fireEvent.click(within(row).getByText(template.description));
  expect(selections).toEqual([{ environment, templateId: template.template_id }]);
});

// 场景：两个环境的新草稿保留来源，StrictMode 重建不会清除主页输入或重复打开保护弹窗。
test.each(["cloud", "local"] as const)("%s 新草稿在 StrictMode 中保留来源信息", async (environment) => {
  render(<StrictMode><TemplateWorkspace selection={creation("旅行模板", environment)} onHome={() => {}} /></StrictMode>);
  const info = await screen.findByRole("region", { name: "模板信息" });
  expect(within(info).getByLabelText("当前环境").textContent).toBe(environment === "cloud" ? "云端" : "本地");
  expect(within(info).getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(screen.queryByRole("dialog")).toBeNull();
});

// 场景：添加文字后清除样式，保存校验失败仍保留创建信息与文字草稿。
test("保存前校验效果，失败保留全部草稿", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "需要保留的标题" } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("请至少选择一个效果");
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("需要保留的标题");
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(screen.getByText("新模板 · 尚未保存")).toBeTruthy();
});

// 场景：重复接收同一次主页输入保留草稿；新建另一模板触发保护，取消保留原状态。
test("重新渲染与取消新建均保留原草稿", async () => {
  const selection = creation();
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await addText();
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "保留标题" } });
  view.rerender(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  view.rerender(<TemplateWorkspace selection={creation("另一模板")} onHome={() => {}} />);
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "取消" }));
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
});

// 场景：新草稿即使尚未改动效果也受到保护，明确放弃才替换环境和所有元信息。
test("放弃未保存的新模板后进入主页指定的新环境", async () => {
  const view = render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await screen.findByRole("region", { name: "模板信息" });
  view.rerender(<TemplateWorkspace selection={{ templateId: null, environment: "local", name: "本地模板", description: "" }} onHome={() => {}} />);
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放弃修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("当前环境").textContent).toBe("本地");
  expect(screen.getByLabelText("模板名称").textContent).toBe("本地模板");
  expect(screen.getByLabelText("模板描述").textContent).toBe("暂无模板描述");
});

// 场景：切回主页再返回模板库，已创建信息和未保存效果继续保留；重新创建需要确认。
test("主页与模板库切换保留未保存内容", async () => {
  render(<HomePage />);
  await createFromHome();
  await addText();
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "保留标题" } });
  fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
  fireEvent.mouseDown(screen.getByRole("tab", { name: "模版编辑" }), { button: 0 });
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  fireEvent.mouseDown(screen.getByRole("tab", { name: "主页" }), { button: 0 });
  await createFromHome("另一模板");
  const dialog = await screen.findByRole("dialog", { name: "保存当前修改？" });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
});
