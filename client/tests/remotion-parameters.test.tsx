/** Remotion 参数面板 v2 行为：按实例渲染嵌套 JSON 参数、校验非法输入并只提交净变化；执行 bun run test。 */
import { expect, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { ParametersPanel } from "@/features/remotion_templates/ParametersPanel";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import type { JsonValue, Values } from "@/features/remotion_templates/model";
import * as api from "@/features/remotion_templates/api";
import { remotionJob, remotionNestedVersion } from "./remotion-fixtures";
import { remotionServer } from "./remotion-server";
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
  await api.create("组合字效");
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
  expect(screen.getByRole("group", { name: "模板参数" })).toBeTruthy();
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
