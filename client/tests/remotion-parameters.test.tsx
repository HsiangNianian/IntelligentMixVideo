/** Remotion 参数面板 v2 行为：按实例渲染嵌套 JSON 参数、校验非法输入并只提交净变化；执行 bun run test。 */
import { expect, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ParametersPanel } from "@/features/remotion_templates/ParametersPanel";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import type { JsonValue, Values } from "@/features/remotion_templates/model";
import * as api from "@/features/remotion_templates/api";
import { remotionJob, remotionNestedVersion } from "./remotion-fixtures";
import { remotionServer, seed } from "./remotion-server";
import { fetchMock } from "./setup";

/** 建立已选中的 v2 会话；版本接口返回嵌套参数版本，不连接真实服务。 */
async function nestedHistory() {
  const fake = remotionServer((path) =>
    /^\/versions\/[^/]+$/.test(path)
      ? Response.json({
          ...remotionNestedVersion(path.split("/")[2]!),
          project_id: "work-1",
        })
      : undefined,
  );
  await seed("组合字效");
  fake.advance({ ...remotionJob("succeeded", "version-2"), id: "job-2" });
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  return fake;
}

/** 只取参数保存请求体，避免依赖请求顺序或其它写入。 */
function parameterPosts(): { parameters?: Values }[] {
  return fetchMock.mock.calls
    .filter(
      ([url, init]) =>
        String(url).endsWith("/messages") && init?.method === "POST",
    )
    .map(([, init]) => JSON.parse(String(init!.body)));
}

/** 受控渲染参数面板并记录回调，用于验证渲染与校验行为。 */
function renderPanel(
  changes: [string, JsonValue][],
  { dirty = false }: { dirty?: boolean } = {},
) {
  const props = {
    version: remotionNestedVersion(),
    values: { title: { text: "今日灵感", size: 64 } },
    disabled: false,
    pending: false,
    saving: false,
    onChange: (key: string, value: JsonValue) => changes.push([key, value]),
    onSave: () => {},
    onDiscard: () => {},
  };
  render(<ParametersPanel {...props} dirty={dirty} />);
}

/** 面板上的保存按钮；非法输入时须保持不可用。 */
function saveButton(): HTMLButtonElement {
  return screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" });
}

test("v2 版本按实例渲染 JSON 文本域并回传解析后的参数", () => {
  const changes: [string, JsonValue][] = [];
  renderPanel(changes);
  const field = screen.getByLabelText<HTMLTextAreaElement>("实例参数 · title");
  expect(field.value).toContain("今日灵感");
  fireEvent.change(field, {
    target: { value: '{"text":"新标题","size":72}' },
  });
  expect(changes).toEqual([["title", { text: "新标题", size: 72 }]]);
});

test("非法 JSON 保留输入、提示错误并阻止保存", () => {
  const changes: [string, JsonValue][] = [];
  renderPanel(changes, { dirty: true });
  expect(saveButton().disabled).toBe(false);
  const field = screen.getByLabelText<HTMLTextAreaElement>("实例参数 · title");
  fireEvent.change(field, { target: { value: '{"text":' } });
  expect(field.value).toBe('{"text":');
  expect(screen.getByRole("alert").textContent).toContain(
    "请输入有效的 JSON 参数。",
  );
  expect(changes).toEqual([]);
  expect(saveButton().disabled).toBe(true);
});

test("v2 保存只提交与已保存值不同的嵌套参数", async () => {
  await nestedHistory();
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  act(() => result.current.change("title", { text: "新标题", size: 64 }));
  expect(result.current.dirty).toBe(true);
  act(() => result.current.saveParameters());
  await waitFor(() => expect(parameterPosts()).toHaveLength(1));
  expect(parameterPosts()[0]!.parameters).toEqual({
    title: { text: "新标题", size: 64 },
  });
});

test("键序不同但等价的 JSON 不产生净变化", async () => {
  await nestedHistory();
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  act(() => result.current.change("title", { size: 64, text: "今日灵感" }));
  expect(result.current.dirty).toBe(false);
  expect(result.current.saveParameters()).toBe(false);
  expect(parameterPosts()).toEqual([]);
});

/** 组合版本：顶层参数有颜色、字号、字重与关键词，实例 title 只剩一个数字参数。 */
function composedVersion() {
  const version = remotionNestedVersion();
  version.candidate.default_config = { text: "今日灵感", color: "#FFFFFF", fontSize: 64, fontWeight: 400, keywords: [], title: { glow: 0.4 } };
  version.candidate.config_schema = {
    properties: {
      text: { type: "string", title: "标题文字", minLength: 1, maxLength: 12 },
      color: { type: "string", title: "颜色", pattern: "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$" },
      fontSize: { type: "number", title: "字号", minimum: 24, maximum: 240 },
      fontWeight: { type: "integer", title: "字重", enum: [400, 700] },
      keywords: { type: "array", title: "关键词", items: { type: "string", maxLength: 4 }, maxItems: 2 },
      title: { type: "object", properties: { glow: { type: "number", title: "光晕", minimum: 0, maximum: 1 } } },
    },
  };
  return version;
}

/** 渲染组合版本的面板并记录回传的参数变化。 */
function renderComposed(changes: [string, JsonValue][]) {
  const version = composedVersion();
  render(<ParametersPanel version={version} values={version.candidate.default_config} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
}

test("组合版本按 Schema 生成类型化控件，实例参数整体回传", () => {
  const changes: [string, JsonValue][] = [];
  renderComposed(changes);
  // 宿主固定的文字、字号、字重与关键词归入「配置」，Agent 设计的颜色归入「样式」。
  expect(within(screen.getByRole("group", { name: "配置" })).getByLabelText("字号")).toBeTruthy();
  expect(within(screen.getByRole("group", { name: "样式" })).getByLabelText("颜色选择器")).toBeTruthy();
  expect(within(screen.getByRole("group", { name: "样式" })).queryByLabelText("关键词") === null).toBe(true);
  fireEvent.change(screen.getByLabelText("标题文字"), { target: { value: "新品上市" } });
  fireEvent.change(screen.getByLabelText("颜色选择器"), { target: { value: "#ff0000" } });
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "96" } });
  fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "新品，上市" } });
  fireEvent.change(screen.getByLabelText("光晕"), { target: { value: "0.8" } });
  expect(changes).toEqual([
    ["text", "新品上市"], ["color", "#ff0000"], ["fontSize", 96], ["keywords", ["新品", "上市"]], ["title", { glow: 0.8 }],
  ]);
  expect(screen.queryByLabelText("实例参数 · title")).toBeNull();
});

/** 双栏文字组合的版本：上下两栏（上栏颜色绑定为顶层参数）、关键词 Preset glow 与一个下栏发光特效 shine（颜色绑定为顶层参数，另有内部参数）。 */
function textBlockVersion() {
  const version = composedVersion();
  version.spec.sprite = {
    kind: "text", text: { tiers: ["upper", "lower"], keyword: "glow", effects: [{ instance_id: "shine", title: "发光" }] },
    bindings: [
      { name: "line1Color", title: "上栏颜色", targets: [{ instance_id: "upper", parameter: "color" }] },
      { name: "shineColor", title: "发光颜色", targets: [{ instance_id: "shine", parameter: "glowColor" }] },
      { name: "keywordEffectColor", title: "关键词颜色效果", targets: [{ instance_id: "glow", parameter: "color" }] },
    ],
  };
  const color = { type: "string" as const, pattern: "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$" };
  const nested = (title: string) => ({ type: "object" as const, properties: { amount: { type: "number" as const, title, minimum: 0, maximum: 1 } } });
  Object.assign(version.candidate.config_schema.properties, {
    line1Color: { ...color, title: "上栏颜色" },
    keywordEffect: { type: "boolean", title: "关键词字效" },
    effect1: { type: "boolean", title: "持续·发光" },
    effect1Target: { type: "string", title: "发光位置", enum: ["block", "tiers", "line1", "line2", "chars", "keywordChars", "plainChars", "keyword"] },
    shineColor: { ...color, title: "发光颜色" },
    keywordEffectColor: { ...color, title: "关键词颜色效果" },
    upper: nested("上栏投影"), lower: nested("下栏投影"), glow: nested("关键词光晕"), shine: nested("脉动速度"),
  });
  Object.assign(version.candidate.default_config, {
    line1Color: "#FFFFFF", keywordEffect: true, effect1: true, effect1Target: "line2", shineColor: "#FFD54A", keywordEffectColor: "#FFAA00",
    upper: { amount: 0.2 }, lower: { amount: 0.4 }, glow: { amount: 0.6 }, shine: { amount: 0.8 },
  });
  return version;
}

// 场景：文字组合的字效右侧只有「文字」与「特效」两块：文字本身的字号、颜色、关键词与各栏内部参数在「文字」里；每个特效（含关键词字效）
// 是一张卡片，开关决定是否应用，位置（中文名称）、绑定在它上面的样式与它的内部参数都在卡片里，不再散在「样式」或折叠的实例里。
test("文字组合的字效分为文字与特效两块，每个特效的开关和配置在同一张卡片里", () => {
  const version = textBlockVersion();
  const changes: [string, JsonValue][] = [];
  render(<ParametersPanel version={version} values={version.candidate.default_config} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
  expect(screen.queryByRole("group", { name: "配置" })).toBeNull();
  expect(screen.queryByRole("group", { name: "样式" })).toBeNull();
  const text = screen.getByRole("group", { name: "文字" });
  for (const label of ["字号", "上栏颜色", "关键词"]) expect(within(text).getByLabelText(label)).toBeTruthy();
  for (const label of ["发光颜色", "脉动速度", "关键词光晕"]) expect(within(text).queryByLabelText(label)).toBeNull();
  expect(Array.from(text.querySelectorAll("summary")).map((item) => item.textContent)).toEqual(["实例 · title", "上栏", "下栏"]);
  const shine = within(screen.getByRole("group", { name: "特效" })).getByRole("region", { name: "持续·发光" });
  expect(within(shine).getByLabelText("发光位置").textContent).toBe("下栏");
  expect(within(shine).getByLabelText("发光颜色")).toBeTruthy();
  const keyword = screen.getByRole("region", { name: "关键词字效" });
  expect(within(keyword).getByLabelText("关键词光晕")).toBeTruthy();
  fireEvent.click(within(shine).getByRole("switch", { name: "应用：持续·发光" }));
  fireEvent.change(within(shine).getByLabelText("脉动速度"), { target: { value: "0.5" } });
  fireEvent.click(within(keyword).getByRole("switch", { name: "应用：关键词字效" }));
  expect(changes).toEqual([["effect1", false], ["shine", { amount: 0.5 }], ["keywordEffect", false]]);
});

// 场景：自由组件版本（Agent 写的 Effect.tsx）没有文字块：宿主的配置与分组为「文字」的样式在「文字」里，其余样式按 Agent 声明的分组各成一张
// 特效卡片，组里的开关是卡片的应用开关；关键词字效开关 keywordEffect 放进「关键词字效」那张卡片。
test("自由组件的样式参数按 Agent 声明的分组成卡片", () => {
  const version = composedVersion();
  version.spec.sprite = { kind: "text", effect: { kind: "text", text: { keywords: ["灵感"] } } };
  const color = { type: "string" as const, pattern: "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$" };
  version.candidate.config_schema.properties = {
    text: { type: "string", title: "文字", minLength: 1, maxLength: 100 },
    fontSize: { type: "number", title: "字号", minimum: 24, maximum: 240 },
    keywords: { type: "array", title: "关键词", items: { type: "string", maxLength: 12 }, maxItems: 20 },
    keywordEffect: { type: "boolean", title: "关键词字效" },
    color: { ...color, title: "文字颜色", "x-imv-group": "文字" },
    riseOn: { type: "boolean", title: "启用", "x-imv-group": "入场·上浮" },
    riseDistance: { type: "number", title: "上浮距离", minimum: 0, maximum: 2, "x-imv-group": "入场·上浮" },
    keywordEffectColor: { ...color, title: "关键词颜色", "x-imv-group": "关键词字效" },
    effect: { type: "object", properties: {} },
  };
  version.candidate.default_config = { text: "今日灵感", fontSize: 96, keywords: [], keywordEffect: true, color: "#FFFFFF", riseOn: true, riseDistance: 0.6, keywordEffectColor: "#FFD54A", effect: {} };
  const changes: [string, JsonValue][] = [];
  render(<ParametersPanel version={version} values={version.candidate.default_config} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
  const text = screen.getByRole("group", { name: "文字" });
  for (const label of ["文字", "字号", "文字颜色", "关键词"]) expect(within(text).getByLabelText(label)).toBeTruthy();
  expect(within(text).queryByLabelText("上浮距离")).toBeNull();
  const rise = within(screen.getByRole("group", { name: "特效" })).getByRole("region", { name: "入场·上浮" });
  fireEvent.click(within(rise).getByRole("switch", { name: "应用：入场·上浮" }));
  fireEvent.change(within(rise).getByLabelText("上浮距离"), { target: { value: "1" } });
  const keyword = screen.getByRole("region", { name: "关键词字效" });
  expect(within(keyword).getByLabelText("关键词颜色")).toBeTruthy();
  fireEvent.click(within(keyword).getByRole("switch", { name: "应用：关键词字效" }));
  expect(changes).toEqual([["riseOn", false], ["riseDistance", 1], ["keywordEffect", false]]);
});

// 场景：特效放在逐字位置（每个字依次显现，如打字机）时，位置显示中文名称（这里是「关键词逐字」），推迟开始的延迟和位置一样在这个特效的卡片里；
// 双栏的排布（重叠）是「文字」里的排版参数，显示中文名称。
test("特效的逐字位置、延迟与双栏的重叠排布显示中文名称", () => {
  const version = textBlockVersion();
  version.candidate.config_schema.properties.tierLayout = { type: "string", title: "双栏排布", enum: ["stack", "overlap"] };
  version.candidate.config_schema.properties.effect1Delay = { type: "number", title: "发光延迟（秒）", minimum: 0, maximum: 3 };
  render(<ParametersPanel version={version} values={{ ...version.candidate.default_config, effect1Target: "keywordChars", effect1Delay: 1, tierLayout: "overlap" }} disabled={false} pending={false} dirty={false} saving={false}
    onChange={() => {}} onSave={() => {}} onDiscard={() => {}} />);
  const shine = within(screen.getByRole("group", { name: "特效" })).getByRole("region", { name: "持续·发光" });
  expect(within(shine).getByLabelText("发光位置").textContent).toBe("关键词逐字");
  expect(within(shine).getByLabelText<HTMLInputElement>("发光延迟（秒）").value).toBe("1");
  expect(within(screen.getByRole("group", { name: "文字" })).queryByLabelText("发光延迟（秒）")).toBeNull();
  expect(within(screen.getByRole("group", { name: "文字" })).getByLabelText("双栏排布").textContent).toBe("重叠");
});

test("超出范围、长度或项数的输入只留在表单并提示", () => {
  const changes: [string, JsonValue][] = [];
  renderComposed(changes);
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "999" } });
  fireEvent.change(screen.getByLabelText("标题文字"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "一，二，三" } });
  expect(changes).toEqual([]);
  expect(screen.getAllByRole("alert").map((item) => item.textContent)).toEqual([
    "请输入有效的标题文字。", "请输入有效的字号。", "请输入有效的关键词（最多 2 个）。",
  ]);
});

test("实例参数的 pattern 在本地检查，不合的输入不进入预览", () => {
  const changes: [string, JsonValue][] = [];
  const version = composedVersion();
  version.candidate.config_schema.properties.title = { type: "object", properties: { code: { type: "string", title: "编号", maxLength: 6, pattern: "^[A-Z]+$" } } };
  render(<ParametersPanel version={version} values={{ ...version.candidate.default_config, title: { code: "AB" } }} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
  fireEvent.change(screen.getByLabelText("编号"), { target: { value: "ab" } });
  expect(screen.getByRole("alert").textContent).toBe("请输入有效的编号。");
  fireEvent.change(screen.getByLabelText("编号"), { target: { value: "XY" } });
  expect(changes).toEqual([["title", { code: "XY" }]]);
});

// 场景：多行字效（声明 lineStartFrames）的文字按行分别填写，用换行连接后回传；任意一行留空都不进入预览；行起点由宿主注入，不出现在面板里。
test("多行字效逐行编辑文字，行起点不在参数面板显示", () => {
  const changes: [string, JsonValue][] = [];
  const version = composedVersion();
  version.candidate.config_schema.properties.text!.maxLength = 100;
  version.candidate.config_schema.properties.lineStartFrames = { type: "array", title: "行起点", items: { type: "integer", minimum: 0, maximum: 3600 }, maxItems: 2 };
  const values = { ...version.candidate.default_config, text: "判断一个人\n选择花字", lineStartFrames: [0, 30] };
  render(<ParametersPanel version={version} values={values} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
  expect((screen.getByLabelText("标题文字第 1 行") as HTMLInputElement).value).toBe("判断一个人");
  fireEvent.change(screen.getByLabelText("标题文字第 2 行"), { target: { value: "选择滤镜" } });
  expect(changes).toEqual([["text", "判断一个人\n选择滤镜"]]);
  fireEvent.change(screen.getByLabelText("标题文字第 1 行"), { target: { value: " " } });
  expect(changes).toHaveLength(1);
  expect(screen.getByRole("alert").textContent).toBe("请输入有效的标题文字（2 行都要填写）。");
  expect(screen.queryByText(/lineStartFrames|行起点/) === null).toBe(true);
});

// 场景：四行双语字效以两组中英分别编辑，修改第二组译文保留第一组，不显示四个中文输入框。
test("双栏双语参数保留两组中英文字", () => {
  const version = textBlockVersion();
  version.spec.sprite!.text = { tiers: ["upper", "upperEn", "lower", "lowerEn"], bilingual: true, effects: [] };
  Object.assign(version.candidate.config_schema.properties, {
    text: { type: "string", title: "文字", minLength: 1, maxLength: 100 },
    textEn: { type: "string", title: "英文文字", minLength: 1, maxLength: 100 },
    lineStartFrames: { type: "array", items: { type: "integer" }, minItems: 4, maxItems: 4 },
  });
  const changes: [string, JsonValue][] = [];
  const values = { ...version.candidate.default_config, text: "做清洁不是因为妆\n而是因为脏", textEn: "First\nSecond", lineStartFrames: [0, 0, 15, 15] };
  render(<ParametersPanel version={version} values={values} disabled={false} pending={false} dirty={false} saving={false}
    onChange={(key, value) => changes.push([key, value])} onSave={() => {}} onDiscard={() => {}} />);
  expect(screen.getByLabelText<HTMLInputElement>("文字第 2 行").value).toBe("而是因为脏");
  expect(screen.queryByLabelText("文字第 3 行")).toBeNull();
  fireEvent.change(screen.getByLabelText("英文文字第 2 行"), { target: { value: "New second" } });
  expect(changes).toEqual([["textEn", "First\nNew second"]]);
});

// 场景：四行双语字效的特效位置必须按“第一组/第二组”显示，不能把第 3、4 行误标成同一栏的中英文。
test("四行双语字效的作用位置显示两组中英文", () => {
  const version = textBlockVersion();
  version.spec.sprite!.text = { tiers: ["upper", "upperEn", "lower", "lowerEn"], bilingual: true, effects: [{ instance_id: "shine", title: "发光" }] };
  version.candidate.config_schema.properties.effect1Target = {
    type: "string", title: "发光位置", enum: ["block", "tiers", "line1", "line2", "line3", "line4"],
  };
  const values = { ...version.candidate.default_config, effect1Target: "line3" };
  render(<ParametersPanel version={version} values={values} disabled={false} pending={false} dirty={false} saving={false}
    onChange={() => {}} onSave={() => {}} onDiscard={() => {}} />);
  const shine = within(screen.getByRole("group", { name: "特效" })).getByRole("region", { name: "持续·发光" });
  expect(within(shine).getByLabelText("发光位置").textContent).toBe("第二组中文");
});
