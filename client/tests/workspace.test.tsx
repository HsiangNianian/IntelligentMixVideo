/** 工作区核心测试：使用真实 React 控件和随包目录检查创建、独立对象与草稿保护；执行 bun run test。 */
import { expect, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import HomePage from "@/pages/HomePage";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { TemplateCollection, type TemplateSelection } from "@/features/templates/TemplateHome";
import { Toaster } from "@/components/ui/sonner";
import { fetchMock, mockDesktop } from "./setup";
import { apiBase, setApiBase } from "@/lib/api-base";
import { recovery, restoreRecovery } from "@/features/updates/recovery";
import { updateBlockers } from "@/lib/update-guard";
import { protobufListResponse, protobufTemplateResponse, savedTemplate, templateSummary } from "./fixtures";

/** 生成独立的主页新建输入，不包含服务端响应或存储数据。 */
function creation(name = "旅行模板", environment: "cloud" | "local" = "cloud"): TemplateSelection {
  return { templateId: null, environment, name, description: "适用于旅行视频" };
}

/** 从左侧真实资产创建文字对象，供后续参数编辑场景使用。 */
async function addText(label = "顶部标题") {
  const assets = await screen.findByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("radio", { name: "花字" }));
  fireEvent.keyDown(within(assets).getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: label }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用花字：/ })[0]);
  fireEvent.click(screen.getByRole("tab", { name: "外观与效果" }));
}

// 场景：云端和本地新模板自带固定文字，重复应用样式保持数量及文字内容。
test.each(["cloud", "local"] as const)("%s 新模板保留唯一标题和字幕", async (environment) => {
  render(<TemplateWorkspace selection={creation("空白模板", environment)} onHome={() => {}} />);
  const applied = await screen.findByRole("region", { name: "已添加特效" });
  expect(within(applied).queryAllByRole("button")).toHaveLength(2);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: "底部字幕" }));
  expect(within(applied).queryAllByRole("button")).toHaveLength(2);
  await addText("底部字幕");
  expect(within(applied).getAllByRole("button")).toHaveLength(2);
  expect(within(applied).getByRole("button", { name: "编辑底部字幕", pressed: true })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "手动添加的字幕" } });
  await addText();
  expect(within(applied).getAllByRole("button")).toHaveLength(2);
  fireEvent.click(within(applied).getByRole("button", { name: "编辑底部字幕" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("手动添加的字幕");
  expect(screen.queryByRole("button", { name: "移除当前画面对象" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "时间设置" })).toBeNull();
  expect(screen.queryByLabelText("轨道时间设置")).toBeNull();
  fireEvent.click(within(applied).getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.queryByRole("button", { name: "移除当前画面对象" })).toBeNull();
  expect(within(applied).queryAllByRole("button")).toHaveLength(2);
});

// 场景：转场设置仅在选中转场时显示，连续添加、切换对象和移除后保留统一参数。
test("转场按添加顺序连接三个视频", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  const assets = screen.getByRole("region", { name: "特效资产" });
  expect(screen.queryByRole("region", { name: "转场设置" })).toBeNull();
  expect(screen.queryByLabelText("视频交界处")).toBeNull();
  fireEvent.click(within(assets).getByRole("radio", { name: "转场" }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用转场：/ })[0]);
  expect(screen.getByRole("button", { name: "编辑镜头转场 1", pressed: true })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "转场 1 效果" })).toBeTruthy();
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用转场：/ })[1]);
  expect(screen.getByRole("button", { name: "编辑镜头转场 2", pressed: true })).toBeTruthy();
  const inspector = screen.getByRole("region", { name: "转场设置" });
  fireEvent.change(within(inspector).getByLabelText("IMS 转场持续时间 / 秒"), { target: { value: "2" } });
  const durationSlider = within(inspector).getByRole("slider", { name: "转场持续时间滑块" });
  expect(durationSlider.getAttribute("aria-valuenow")).toBe("2");
  fireEvent.keyDown(durationSlider, { key: "ArrowLeft" });
  expect(screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒").value).toBe("1.9");
  fireEvent.keyDown(durationSlider, { key: "ArrowRight" });
  expect(screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒").value).toBe("2");
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.getByRole("tab", { name: "外观与效果" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "转场设置" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "编辑镜头转场 2" }));
  expect(screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒").value).toBe("2");
  expect(screen.getByRole("combobox", { name: "转场 2 效果" })).toBeTruthy();
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用转场：/ })[2]);
  expect(screen.queryByRole("dialog", { name: "无法添加特效" })).toBeNull();
  expect(screen.getByText("转场 3")).toBeTruthy();
  expect(within(screen.getByRole("region", { name: "已添加特效" })).getAllByRole("button", { name: /编辑镜头转场/ })).toHaveLength(3);
  expect(screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒").value).toBe("2");
  fireEvent.click(screen.getByRole("radio", { name: "随机选择" }));
  expect(screen.getByRole("radio", { name: "随机选择" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("combobox", { name: "转场 2 效果" }));
  expect(screen.queryByRole("option", { name: "无转场" })).toBeNull();
  fireEvent.keyDown(screen.getByPlaceholderText("搜索转场"), { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "删除转场 2" }));
  expect(screen.queryByRole("button", { name: "编辑镜头转场 3" })).toBeNull();
  expect(screen.queryByRole("region", { name: "画面对象设置" })).toBeNull();
  expect(screen.getByRole("region", { name: "转场设置" })).toBeTruthy();
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用转场：/ })[1]);
  expect(screen.getByRole("button", { name: "编辑镜头转场 3", pressed: true })).toBeTruthy();
  for (let index = 3; index >= 1; index -= 1) fireEvent.click(screen.getByRole("button", { name: `删除转场 ${index}` }));
  expect(screen.queryAllByRole("button", { name: /编辑镜头转场/ })).toHaveLength(0);
  expect(screen.getByText("尚未添加转场，三个视频连续播放。")).toBeTruthy();
});

// 场景：时间输入尚未完整时停止保存；修正后使用最新草稿继续执行模板校验。
test("空白时间阻止保存，修正后恢复保存流程", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  fireEvent.click(screen.getByRole("tab", { name: "时间设置" }));
  const input = await screen.findByLabelText("开始时间 / 秒");
  fireEvent.change(input, { target: { value: "" } });
  const error = "开始时间须为非负秒数，或 0% 至小于 100% 的百分比";
  expect(screen.getByText(error)).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  expect(screen.getByText(error)).toBeTruthy();
  expect(screen.queryByText("请至少选择一个效果")).toBeNull();
  fireEvent.change(input, { target: { value: "2" } });
  expect(screen.queryByText(error)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "关闭特效设置" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("2");
});

// 场景：转场时长清空后阻止保存，输入超出上限时恢复为有效值并同步保留。
test("转场时长清空后阻止保存并支持修正", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  const assets = await screen.findByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("radio", { name: "转场" }));
  fireEvent.click(within(assets).getAllByRole("button", { name: /^应用转场：/ })[0]);
  const input = screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒");
  fireEvent.change(input, { target: { value: "" } });
  const error = "转场持续时间为 0.1～3 秒";
  expect(screen.getByText(error)).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  expect(screen.getByText(error)).toBeTruthy();
  expect(screen.queryByText("正在保存…")).toBeNull();
  fireEvent.change(input, { target: { value: "5" } });
  expect(input.value).toBe("3");
  expect(screen.queryByText(error)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑镜头转场 1" }));
  expect(screen.getByLabelText<HTMLInputElement>("IMS 转场持续时间 / 秒").value).toBe("3");
});

// 场景：往返经过 1280px 时保留未完成输入、焦点及预览节点，空白时间继续阻止保存。
test("跨越布局断点保留输入草稿和时间校验", async () => {
  const originalWidth = window.innerWidth;
  Reflect.set(window, "innerWidth", 1200);
  const view = render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  try {
    await addText();
    fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
    fireEvent.click(screen.getByRole("tab", { name: "时间设置" }));
    const start = screen.getByLabelText<HTMLInputElement>("开始时间 / 秒");
    fireEvent.change(start, { target: { value: "" } });
    const preview = screen.getByRole("region", { name: "实时预览" });
    const error = "开始时间须为非负秒数，或 0% 至小于 100% 的百分比";
    start.focus();

    for (const width of [1279, 1280, 1400, 1280, 1279, 1200]) {
      Reflect.set(window, "innerWidth", width);
      fireEvent(window, new Event("resize"));
      expect(screen.getByLabelText("开始时间 / 秒")).toBe(start);
      expect(start.value).toBe("");
      expect(document.activeElement).toBe(start);
      expect(screen.getByRole("region", { name: "实时预览" })).toBe(preview);
      fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
      expect(screen.getByText(error)).toBeTruthy();
      expect(screen.queryByText("请至少选择一个效果")).toBeNull();
    }

    fireEvent.change(start, { target: { value: "2" } });
    expect(screen.queryByText(error)).toBeNull();
    expect(start.value).toBe("2");
  } finally {
    view.unmount();
    Reflect.set(window, "innerWidth", originalWidth);
    fireEvent(window, new Event("resize"));
  }
});

// 场景：同类特效各有独立参数面板，时间修改、非法范围与删除均保持其他实例。
test("重复添加特效后分别设置时间并删除指定实例", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await screen.findByRole("region", { name: "特效资产" });
  const assets = screen.getByRole("region", { name: "特效资产" });
  fireEvent.click(within(assets).getByRole("radio", { name: "滤镜" }));
  const asset = within(assets).getAllByRole("button", { name: /^应用滤镜：/ })[0];
  fireEvent.click(asset);
  fireEvent.change(screen.getByLabelText("开始时间 / 秒"), { target: { value: "2" } });
  fireEvent.keyDown(screen.getByRole("combobox", { name: "持续方式" }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: "固定时长" }));
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "3" } });
  fireEvent.click(asset);
  expect(screen.getByRole("button", { name: "编辑视频滤镜 2", pressed: true })).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "编辑视频滤镜 1" }));
  expect(screen.getByLabelText<HTMLInputElement>("开始时间 / 秒").value).toBe("2");
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "0" } });
  expect(screen.getByText("持续时间须大于零")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "外观与效果" }));
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  fireEvent.click(screen.getByRole("button", { name: "编辑视频滤镜" }));
  fireEvent.click(screen.getByRole("tab", { name: "时间设置" }));
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
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(screen.queryByLabelText("示例文字")).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByLabelText("模板名称").textContent).toBe("另一模板");
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("让每一帧 都有风格");
  expect(within(screen.getByRole("region", { name: "已添加特效" })).queryAllByRole("button")).toHaveLength(2);
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

// 场景：标题和字幕的关键词设置独立保存于各自的画面对象。
test("关键词页签分别编辑标题和字幕", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  expect(screen.queryByRole("textbox", { name: "指定关键词" })).toBeNull();
  expect(screen.queryByText("关键词预览")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "加粗" }));
  expect(screen.getByRole("button", { name: "加粗" }).getAttribute("aria-pressed")).toBe("true");
  await addText("底部字幕");
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  expect(screen.queryByRole("textbox", { name: "指定关键词" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "斜体" }));
  expect(screen.getByRole("button", { name: "斜体" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  expect(screen.queryByRole("textbox", { name: "指定关键词" })).toBeNull();
  expect(screen.getByRole("button", { name: "加粗" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "斜体" }).getAttribute("aria-pressed")).toBe("false");
});

// 场景：从标题时间页切换到字幕时显示外观页，字幕保留关键词页，返回标题后仍可编辑时间。
test("底部字幕仅提供外观与关键词页签", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await screen.findByRole("region", { name: "画面对象设置" });
  expect(screen.getByRole("tab", { name: "时间设置", selected: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "编辑底部字幕" }));
  expect(within(screen.getByRole("region", { name: "画面对象设置" })).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["外观与效果", "关键词设置"]);
  expect(screen.getByRole("tab", { name: "外观与效果", selected: true })).toBeTruthy();
  expect(screen.getByLabelText("示例文字")).toBeTruthy();
  expect(screen.queryByLabelText("轨道时间设置")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "关键词设置" }));
  expect(screen.getByRole("button", { name: "加粗" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "编辑顶部标题" }));
  fireEvent.click(screen.getByRole("tab", { name: "时间设置" }));
  expect(screen.getByLabelText("开始时间 / 秒")).toBeTruthy();
});

// 场景：弹窗同时提供三个来源地址，取消后保留当前来源，空地址加载失败时保留输入。
test("播放控制栏弹窗设置三个预览视频", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await screen.findByRole("region", { name: "实时预览" });
  const trigger = screen.getByRole("button", { name: "加载预览视频" });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("dialog", { name: "加载预览视频" })).toBeNull();
  fireEvent.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  const dialog = screen.getByRole("dialog", { name: "加载预览视频" });
  const inputs = [1, 2, 3].map((index) => within(dialog).getByRole<HTMLInputElement>("textbox", { name: `视频 ${index} URL` }));
  const original = inputs.map((input) => input.value);
  expect(original.every(Boolean)).toBe(true);
  inputs.forEach((input, index) => fireEvent.change(input, { target: { value: `/video-${index}.mp4` } }));
  fireEvent.change(inputs[1], { target: { value: "" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "加载" }));
  await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("请输入视频 2 的 URL"));
  expect(inputs[0].value).toBe("/video-0.mp4");
  expect(inputs[2].value).toBe("/video-2.mp4");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog", { name: "加载预览视频" })).toBeNull();
  fireEvent.click(trigger);
  expect([1, 2, 3].map((index) => screen.getByRole<HTMLInputElement>("textbox", { name: `视频 ${index} URL` }).value)).toEqual(original);
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

// 场景：导航通过 aria-current 表达当前页，切换后编辑入口可用。
test("工作室导航标记当前页面", () => {
  render(<HomePage />);
  const home = screen.getByRole("button", { name: "主页" });
  const editor = screen.getByRole("button", { name: "模板编辑" });
  expect(home.getAttribute("aria-current")).toBe("page");
  fireEvent.click(editor);
  expect(editor.getAttribute("aria-current")).toBe("page");
  expect(home.getAttribute("aria-current")).toBeNull();
});

// 场景：更新前保存的旧导航标识仍可恢复到新版工作区，下一次更新继续保存兼容标识。
test.each([
  ["library", "模板编辑"], ["remotion", "AI 字效"], ["fonts", "字体库"],
] as const)("更新后恢复 %s 工作区", (workspace, label) => {
  restoreRecovery({ workspace });
  render(<HomePage />);
  expect(screen.getByRole("button", { name: label }).getAttribute("aria-current")).toBe("page");
  expect(recovery.workspace).toBe(workspace);
  fireEvent.click(screen.getByRole("button", { name: "主页" }));
  expect(recovery.workspace).toBe("home");
});

// 场景：更新后恢复已保存模板，返回主页仅隐藏编辑器，模板身份仍供下一次更新恢复。
test("新版导航恢复已保存模板且切页保留位置", async () => {
  const template = savedTemplate();
  const selection = { environment: "cloud" as const, templateId: template.template_id };
  restoreRecovery({ workspace: "library", template: selection });
  fetchMock.mockImplementation((async (_url) => protobufTemplateResponse(template, "get")) as typeof fetch);
  render(<HomePage />);
  expect((await screen.findByLabelText("模板名称")).textContent).toBe(template.name);
  expect(recovery.template).toEqual(selection);
  fireEvent.click(screen.getByRole("button", { name: "主页" }));
  expect(recovery.template).toEqual(selection);
  fireEvent.click(screen.getByRole("button", { name: "模板编辑" }));
  expect(screen.getByLabelText("模板名称").textContent).toBe(template.name);
});

// 场景：新工作室弹窗中的未提交输入阻止自动更新，关闭后才解除对话框门禁。
test("新建模板弹窗沿用更新保护", async () => {
  render(<HomePage />);
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  const dialog = await screen.findByRole("dialog", { name: "新建云端模板" });
  fireEvent.change(within(dialog).getByLabelText("模板名称"), { target: { value: "尚未提交的模板" } });
  expect(updateBlockers()).toContain("请先完成或关闭其他对话框");
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  await waitFor(() => expect(updateBlockers()).not.toContain("请先完成或关闭其他对话框"));
});

// 场景：直接进入模板库显示主页入口，主页与 Remotion 页头都不显示时钟。
test("未选择模板时通过主页开始创作", async () => {
  render(<HomePage />);
  expect(screen.getByRole("button", { name: "主页" }).getAttribute("aria-current")).toBe("page");
  expect(screen.queryByRole("region", { name: "当前时间" })).toBeNull();
  // Remotion 页头同样不显示当前时间。
  fireEvent.click(screen.getByRole("button", { name: "AI 字效" }));
  expect(screen.queryByRole("region", { name: "当前时间" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "模板编辑" }));
  expect(screen.getByText("请从主页选择已有模板或创建新模板。")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "保存模板" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "前往主页" }));
  expect(screen.getByRole("button", { name: "主页" }).getAttribute("aria-current")).toBe("page");
  fireEvent.mouseDown(screen.getByRole("tab", { name: "本地模板" }), { button: 0, ctrlKey: false });
  expect(screen.getByText("浏览器无法读取电脑上的文件，请在桌面客户端中查看和新建本地模板。")).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "新建本地模板" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "新建云端模板" }).disabled).toBe(false);
});

// 场景：主页填写信息后跳转，模板信息默认展示文本及铅笔入口，并明确新模板尚未保存。
test("主页创建后显示环境、名称与描述及编辑入口", async () => {
  render(<HomePage />);
  const info = await createFromHome("  旅行模板  ");
  expect(screen.getByRole("button", { name: "模板编辑" }).getAttribute("aria-current")).toBe("page");
  expect(within(info).getByLabelText("当前环境").textContent).toBe("云端");
  expect(within(info).getByLabelText("模板名称").textContent).toBe("旅行模板");
  expect(within(info).getByLabelText("模板描述").textContent).toBe("适用于旅行视频");
  expect(within(info).queryByRole("textbox")).toBeNull();
  expect(within(info).queryByRole("combobox")).toBeNull();
  expect(within(info).getByRole("button", { name: "编辑模板名称" })).toBeTruthy();
  expect(within(info).getByRole("button", { name: "编辑模板描述" })).toBeTruthy();
  expect(within(info).getByRole("button", { name: "保存模板" })).toBeTruthy();
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
  expect(screen.getByRole("button", { name: "主页" }).getAttribute("aria-current")).toBe("page");
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
  render(<TemplateCollection collections={[{ environment,
    collection: { templates: [templateSummary(template)], loading: false, error: "", unavailable: false, refresh: () => {} } }]}
    onSelect={(selection) => selections.push(selection)} />);
  const row = screen.getByRole("button", { name: `选择模板：${template.name}` });
  expect(screen.getByRole("button", { name: `选择模板：${template.name}` })).toBeTruthy();
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

// 场景：空白文字不能保存，输入草稿和模板信息保留。
test("保存前校验文字内容，失败保留全部草稿", async () => {
  render(<TemplateWorkspace selection={creation()} onHome={() => {}} />);
  await addText();
  fireEvent.click(screen.getByRole("button", { name: "重置特效设置" }));
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "   " } });
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText("文字轨道内容不能为空");
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
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
  fireEvent.click(screen.getByRole("button", { name: "主页" }));
  fireEvent.click(screen.getByRole("button", { name: "模板编辑" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
  expect(screen.getByLabelText("模板名称").textContent).toBe("旅行模板");
  fireEvent.click(screen.getByRole("button", { name: "主页" }));
  await createFromHome("另一模板");
  const dialog = await screen.findByRole("dialog", { name: "保存当前修改？" });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留标题");
});

// 场景：已有模板保存成功后以轻提示反馈；图标按钮聚焦时显示悬停提示。
test("保存成功显示轻提示，图标按钮提供悬停提示", async () => {
  const template = savedTemplate();
  fetchMock.mockImplementation((async (_url, init) =>
    protobufTemplateResponse(template, init?.method === "POST" ? "save" : "get")) as typeof fetch);
  render(<><TemplateWorkspace selection={{ environment: "cloud", templateId: template.template_id }} onHome={() => {}} /><Toaster /></>);
  await screen.findByLabelText("模板信息");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await screen.findByText(`模板「${template.name}」已保存`);
  fireEvent.focus(screen.getByRole("button", { name: "全屏预览" }));
  expect((await screen.findByRole("tooltip")).textContent).toBe("全屏预览");
});

// 场景：删除另一个库的模板不影响草稿，删除当前模板才清空编辑区。
test("主页删除当前模板后清空保留的编辑区", async () => {
  const template = savedTemplate();
  fetchMock.mockResolvedValue(protobufTemplateResponse(template, "get"));
  const selection = { environment: "cloud" as const, templateId: template.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  view.rerender(<TemplateWorkspace selection={selection} deletedTemplate={{ ...selection, environment: "local" }} onHome={() => {}} />);
  expect(screen.getByLabelText("模板名称").textContent).toBe("已有模板");
  view.rerender(<TemplateWorkspace selection={selection} deletedTemplate={selection} onHome={() => {}} />);
  expect(await screen.findByText("还没有打开模板")).toBeTruthy();
});

// 场景：删除正在读取的模板后，迟到的 HTTP/IPC 成功或失败回执均不能恢复模板或错误态。
test.each([
  ["cloud", "success"], ["cloud", "missing"],
  ["local", "success"], ["local", "missing"],
] as const)("%s 删除读取中的模板后忽略 %s 回执", async (environment, outcome) => {
  const template = savedTemplate();
  let resolve!: (value: typeof template) => void;
  let reject!: (reason: Error) => void;
  const response = new Promise<typeof template>((done, fail) => { resolve = done; reject = fail; });
  let signal: AbortSignal | null | undefined;
  if (environment === "local") mockDesktop(async () => response);
  else fetchMock.mockImplementation((async (_url, init) => {
    signal = init?.signal;
    try { return protobufTemplateResponse(await response, "get"); }
    catch { return new Response(JSON.stringify({ detail: "模板不存在" }), { status: 404 }); }
  }) as typeof fetch);
  const selection = { environment, templateId: template.template_id };
  const view = render(<TemplateWorkspace selection={selection} onHome={() => {}} />);
  await screen.findByText("正在读取模板…");
  for (const unrelated of [
    { ...selection, environment: environment === "cloud" ? "local" as const : "cloud" as const },
    { ...selection, templateId: "00000000-0000-4000-8000-000000000002" },
  ]) {
    view.rerender(<TemplateWorkspace selection={selection} deletedTemplate={unrelated} onHome={() => {}} />);
    expect(screen.getByText("正在读取模板…")).toBeTruthy();
    if (environment === "cloud") expect(signal?.aborted).toBe(false);
  }
  view.rerender(<TemplateWorkspace selection={selection} deletedTemplate={selection} onHome={() => {}} />);
  expect(screen.queryByText("正在读取模板…")).toBeNull();
  await act(async () => {
    if (outcome === "success") resolve(template);
    else reject(new Error("模板不存在"));
  });
  expect(screen.queryByRole("alert")?.textContent).toBeUndefined();
  expect(screen.queryByRole("button", { name: "重试打开模板" })).toBeNull();
  expect(screen.queryByText("正在读取模板…")).toBeNull();
  expect(screen.getByText("还没有打开模板")).toBeTruthy();
  if (environment === "cloud") expect(signal?.aborted).toBe(true);
  view.rerender(<TemplateWorkspace selection={creation("删除后新建")} deletedTemplate={selection} onHome={() => {}} />);
  expect((await screen.findByLabelText("模板名称")).textContent).toBe("删除后新建");
});

// 场景：已有模板与待打开模板不同，删除其中一个只清理对应草稿或读取，另一个仍可使用。
test.each(["current", "pending"] as const)("切换读取期间删除 %s 模板不影响另一个模板", async (deleting) => {
  const current = savedTemplate();
  const pending = { ...current, template_id: "00000000-0000-4000-8000-000000000002", name: "另一模板" };
  let finish!: (response: Response) => void;
  const response = new Promise<Response>((resolve) => { finish = resolve; });
  fetchMock.mockImplementation((async (url) => String(url).endsWith(current.template_id)
    ? protobufTemplateResponse(current, "get") : response) as typeof fetch);
  const currentSelection = { environment: "cloud" as const, templateId: current.template_id };
  const nextSelection = { ...currentSelection, templateId: pending.template_id };
  const view = render(<TemplateWorkspace selection={currentSelection} onHome={() => {}} />);
  await screen.findByLabelText("模板名称");
  view.rerender(<TemplateWorkspace selection={nextSelection} onHome={() => {}} />);
  await screen.findByText("正在读取模板…");
  view.rerender(<TemplateWorkspace selection={nextSelection}
    deletedTemplate={deleting === "current" ? currentSelection : nextSelection} onHome={() => {}} />);
  if (deleting === "current") expect(screen.getByText("正在读取模板…")).toBeTruthy();
  else expect(screen.queryByText("正在读取模板…")).toBeNull();
  await act(async () => { finish(deleting === "current" ? protobufTemplateResponse(pending, "get")
    : new Response(JSON.stringify({ detail: "模板不存在" }), { status: 404 })); });
  expect(screen.getByLabelText("模板名称").textContent).toBe(deleting === "current" ? pending.name : current.name);
  expect(screen.queryByRole("alert")?.textContent).toBeUndefined();
  expect(screen.queryByRole("button", { name: "重试打开模板" })).toBeNull();
});

// 场景：隐藏的编辑器仍保留草稿，但只在编辑页响应保存快捷键；导航同步保存状态。
test("保存快捷键只作用于当前编辑页并同步导航状态", async () => {
  const states: { name: string; dirty: boolean }[] = [];
  const template = { ...savedTemplate(), name: "旅行模板" };
  fetchMock.mockImplementation((async (_url, init) => protobufTemplateResponse(template, init?.method === "POST" ? "save" : "get")) as typeof fetch);
  const selection = creation();
  const report = (state: { name: string; dirty: boolean } | null) => { if (state) states.push(state); };
  const view = render(<TemplateWorkspace selection={selection} active={false} onHome={() => {}} onEditingState={report} />);
  await screen.findByRole("region", { name: "模板信息" });
  expect(states.at(-1)?.dirty).toBe(true);
  fireEvent.keyDown(window, { key: "s", ctrlKey: true });
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  view.rerender(<TemplateWorkspace selection={selection} active onHome={() => {}} onEditingState={report} />);
  fireEvent.keyDown(window, { key: "s", ctrlKey: true });
  await waitFor(() => expect(states.at(-1)?.dirty).toBe(false));
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

// 场景：设置切换服务地址后，主页重新读取真实列表；状态胶囊只显示连接状态，不显示地址。
test("服务地址变化后刷新主页模板列表", async () => {
  const original = apiBase();
  fetchMock.mockImplementation((async (_url, _init) => protobufListResponse([])) as typeof fetch);
  const view = render(<HomePage />);
  try {
    await screen.findByRole("button", { name: /服务已连接/ });
    act(() => { setApiBase("http://new-api.test:9000"); view.rerender(<HomePage />); });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "http://new-api.test:9000/template")).toBe(true));
    const status = await screen.findByRole("button", { name: "服务已连接，点击打开设置" });
    expect(status.textContent).toBe("已连接");
  } finally { view.unmount(); setApiBase(original); }
});
