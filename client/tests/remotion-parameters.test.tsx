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
