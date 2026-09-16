/** Remotion 会话核心测试：独立验证成功结果、参数互斥与失败恢复；执行 bun run test。 */
import { expect, test } from "bun:test";
import { StrictMode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import type { Values } from "@/features/remotion_templates/model";
import { fetchMock } from "./setup";
import { remotionServer } from "./remotion-server";
import { remotionJob } from "./remotion-fixtures";
import * as api from "@/features/remotion_templates/api";

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

// 开发模式重新挂载 effect 后仍恢复历史及 SSE，同会话点击不能让恢复停留在加载状态。
test("StrictMode 恢复已选择会话并清理重建的订阅", async () => {
  const fake = remotionServer();
  await api.create("已有模板");
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  const { result, unmount } = renderHook(() => useTemplateSession(() => {}), {
    wrapper: StrictMode,
  });
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  expect(result.current.loading).toBe(false);
  expect(fake.streams.size).toBe(1);
  act(() => result.current.select("work-1"));
  expect(result.current.loading).toBe(false);
  unmount();
  expect(fake.streams.size).toBe(0);
});

// 连续编辑累积草稿，显式保存时锁定重复提交，失败后保留最终值和旧代码。
test("会话核心批量保存并拒绝保存期间的修改", async () => {
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
  expect(result.current.values.size).toBe(96);
  expect(edits).toHaveLength(0);
  act(() => {
    result.current.saveParameters();
    result.current.saveParameters();
    result.current.change("size", 120);
  });
  await waitFor(() => expect(edits).toHaveLength(1), { timeout: 2000 });
  expect(edits[0].size).toBe(96);
  await act(async () => finish!(Response.json(remotionJob("failed"))));
  await waitFor(() => expect(result.current.busy).toBeNull());
  expect(result.current.values.size).toBe(96);
  expect(result.current.code).toBe(code);
  expect(result.current.dirty).toBe(true);
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

// 首次快照产物不可用仍订阅后续消息；断线从已消费游标续传，不反复下载失败产物。
test("快照产物失败不阻塞 SSE 与断线续传", async () => {
  let downloads = 0;
  const fake = remotionServer((path) => {
    if (path.endsWith("/artifacts/Export.tsx")) {
      downloads++;
      return new Response(null, { status: 404 });
    }
  });
  const { result, unmount } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("首次模板"));
  await waitFor(() => expect(result.current.error).toContain("新版本读取失败"));
  expect(result.current.loading).toBe(false);
  expect(result.current.busy).toBeNull();
  expect(result.current.version).toBeNull();
  expect(result.current.code).toBe("");
  expect(result.current.retryMode).toBe("version");
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () =>
    fake.advance({
      ...remotionJob("answered"),
      id: "answer-2",
      message: "后续事件已到达",
    }),
  );
  await waitFor(() =>
    expect(
      result.current.messages.some((m) => m.text === "后续事件已到达"),
    ).toBe(true),
  );
  const cursor = fake.records.at(-1)!.id;
  await act(async () => {
    for (const stream of fake.streams)
      stream.controller.error(new TypeError("offline"));
    fake.streams.clear();
  });
  await waitFor(() => expect(result.current.connection).toBe("live"), {
    timeout: 3000,
  });
  const streamUrl = fetchMock.mock.calls
    .filter((call) => String(call[0]).includes("/stream?"))
    .at(-1)![0];
  expect(new URL(String(streamUrl)).searchParams.get("after")).toBe(
    String(cursor),
  );
  expect(downloads).toBe(1);
  expect(result.current.error).toContain("新版本读取失败");
  expect(result.current.busy).toBeNull();
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
  unmount();
  expect(fake.streams.size).toBe(0);
});

// 同一轮直接调用 hook 也必须阻止恢复与待提交参数竞争，不能只依赖按钮 disabled。
test("hook 在参数待提交及读取期间拒绝重复恢复", async () => {
  let release: ((response: Response) => void) | undefined;
  let holdRead = false;
  const fake = remotionServer((path) => {
    if (holdRead && path.endsWith("/session"))
      return new Promise((resolve) => {
        release = resolve;
      });
    if (path.endsWith("/messages")) return new Response(null, { status: 503 });
    if (path.includes("version-2") && path.endsWith("/artifacts/Export.tsx"))
      return new Response(null, { status: 404 });
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("初始标题"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () => fake.advance(remotionJob("succeeded", "version-2")));
  await waitFor(() => expect(result.current.retryMode).toBe("version"));
  const reads = () =>
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/session"))
      .length;
  const before = reads();
  act(() => {
    result.current.change("size", 80);
    result.current.retry();
  });
  expect(reads()).toBe(before);
  act(() => result.current.saveParameters());
  await waitFor(() => expect(result.current.retryMode).toBe("read"), {
    timeout: 2000,
  });
  holdRead = true;
  act(() => {
    result.current.retry();
    result.current.retry();
    result.current.change("size", 96);
  });
  expect(reads()).toBe(before + 1);
  expect(result.current.loading).toBe(true);
  expect(result.current.values.size).toBe(80);
  await act(async () => release!(Response.json(fake.snapshots.get("work-1"))));
  await waitFor(() => expect(result.current.loading).toBe(false));
});

// SSE 接收更新版本后不得把旧基线草稿提交到新版本，也不会留下自动保存定时器。
test("收到新版本后不提交旧基线草稿", async () => {
  const fake = remotionServer();
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("初始标题"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  await waitFor(() => expect(fake.streams.size).toBe(1));
  act(() => result.current.change("size", 80));
  await act(async () => fake.advance(remotionJob("succeeded", "version-2")));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 700)));
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(0);
  expect(result.current.values.size).toBe(64);
});

// 未保存草稿切换需显式选择；取消保留草稿，放弃不发起任务，选择自身不清空。
test.each([null, "work-2"])(
  "核心切换保护支持取消和放弃：%s",
  async (target) => {
    remotionServer();
    await api.create("原会话");
    await api.create("目标会话");
    const { result } = renderHook(() => useTemplateSession(() => {}));
    act(() => result.current.select("work-1"));
    await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
    act(() => {
      result.current.change("size", 80);
      result.current.select("work-1");
    });
    expect(result.current.navigation).toBeNull();
    act(() => result.current.select(target));
    expect(result.current.navigation?.work).toBe(target);
    expect(result.current.workId).toBe("work-1");
    act(() => result.current.resolveNavigation("cancel"));
    expect(result.current.values.size).toBe(80);
    expect(result.current.navigation).toBeNull();
    act(() => {
      result.current.select(target);
      result.current.resolveNavigation("discard");
    });
    await waitFor(() => expect(result.current.workId).toBe(target));
    expect(
      fetchMock.mock.calls.filter((call) =>
        String(call[0]).endsWith("/messages"),
      ),
    ).toHaveLength(0);
  },
);

// 保存并切换等待后台任务和新版本；失败保留原草稿，重试成功才离开，旧回执不污染目标会话。
test.each([null, "work-2"])(
  "保存成功后才切换，失败可继续编辑：%s",
  async (target) => {
    const fake = responses(() =>
      Response.json({ ...remotionJob("running"), id: "saving" }),
    );
    await api.create("原会话");
    await api.create("目标会话");
    const { result } = renderHook(() => useTemplateSession(() => {}));
    act(() => result.current.select("work-1"));
    await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
    act(() => {
      result.current.change("size", 80);
      result.current.select(target);
      result.current.resolveNavigation("save");
    });
    await waitFor(() => expect(result.current.job?.id).toBe("saving"));
    await waitFor(() => expect(result.current.connection).toBe("live"));
    expect(result.current.workId).toBe("work-1");
    expect(result.current.navigation?.saving).toBe(true);
    await act(async () =>
      fake.advance({ ...remotionJob("failed"), id: "saving" }),
    );
    await waitFor(() => expect(result.current.busy).toBeNull());
    expect(result.current.navigation?.saving).toBe(false);
    expect(result.current.values.size).toBe(80);
    expect(result.current.dirty).toBe(true);
    act(() => result.current.resolveNavigation("save"));
    await waitFor(() => expect(result.current.job?.status).toBe("running"));
    await waitFor(() => expect(result.current.connection).toBe("live"));
    await act(async () =>
      fake.advance({
        ...remotionJob("succeeded", "saved-version"),
        id: "saving",
      }),
    );
    await waitFor(() => expect(result.current.workId).toBe(target));
    if (target)
      await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
    else expect(result.current.version).toBeNull();
    expect(result.current.navigation).toBeNull();
    expect(
      fetchMock.mock.calls.filter((call) =>
        String(call[0]).endsWith("/messages"),
      ),
    ).toHaveLength(2);
  },
);

// 存在草稿时只读恢复不能覆盖编辑，卸载后也没有自动保存定时器或后续写入。
test("连续修改、撤销与卸载均不自动保存", async () => {
  remotionServer();
  const { result, unmount } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("标题"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  act(() => {
    result.current.change("size", 80);
    result.current.change("color", "#000000");
    result.current.change("size", 64);
    result.current.change("missing", 1);
    result.current.change("size", Number.NaN);
  });
  expect(result.current.values).toMatchObject({ size: 64, color: "#000000" });
  expect(result.current.values).not.toHaveProperty("missing");
  act(() => result.current.discardParameters());
  expect(result.current.dirty).toBe(false);
  expect(result.current.values.color).toBe("#FFFFFF");
  act(() => result.current.change("size", 80));
  unmount();
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(0);
});
