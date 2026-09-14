/** Remotion 会话核心测试：独立验证成功结果、参数互斥与失败恢复；执行 bun run test。 */
import { expect, test } from "bun:test";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import type { Values } from "@/features/remotion_templates/model";
import { fetchMock } from "./setup";
import { remotionServer } from "./remotion-server";
import { remotionJob } from "./remotion-fixtures";

/** 仅提供核心请求需要的响应；记录实际 HTTP 载荷并阻止真实网络。 */
function responses(
  edit: (body: {
    parameters: Values;
    base_version_id: string;
  }) => Response | Promise<Response>,
) {
  return remotionServer((path, options) => {
    if (path.endsWith("/messages"))
      return edit(JSON.parse(String(options?.body)));
  });
}

// 同一事件批次重复修改不能绕过 UI 锁，失败后恢复成功参数且代码保持不变。
test("会话核心拒绝渲染期间的第二次修改", async () => {
  const edits: Values[] = [];
  let finish: ((response: Response) => void) | undefined;
  responses((body) => {
    expect(body.base_version_id).toBe("version-1");
    edits.push(body.parameters);
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("静态标题"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  const code = result.current.code;
  act(() => {
    result.current.change("size", 80);
    result.current.change("size", 96);
    result.current.send("不能发出的修改");
  });
  expect(result.current.values.size).toBe(80);
  await waitFor(() => expect(edits).toHaveLength(1), { timeout: 2000 });
  expect(edits[0].size).toBe(80);
  await act(async () => finish!(Response.json(remotionJob("failed"))));
  await waitFor(() => expect(result.current.busy).toBeNull());
  expect(result.current.values.size).toBe(64);
  expect(result.current.code).toBe(code);
  expect(result.current.dirty).toBe(false);
  expect(
    result.current.messages.filter((message) => message.role === "user"),
  ).toHaveLength(2);
});

// 新会话清空成功结果和上下文引用，下一次发送创建全新作品而不是修改旧版本。
test("核心新增清空状态并重新创建作品", async () => {
  responses(() => {
    throw new Error("不应修改旧作品");
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("第一个标题"));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  act(() => result.current.reset());
  expect(result.current.workId).toBeNull();
  expect(result.current.version).toBeNull();
  expect(result.current.messages).toEqual([]);
  expect(result.current.values).toEqual({});
  expect(result.current.code).toBe("");
  act(() => result.current.send("第二个标题"));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  expect(
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/works")),
  ).toHaveLength(2);
  expect(result.current.messages[0].text).toBe("第二个标题");
});
