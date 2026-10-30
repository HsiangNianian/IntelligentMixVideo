/** Remotion 会话核心测试：独立验证成功结果、参数互斥与失败恢复；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { StrictMode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import type { Values } from "@/features/remotion_templates/model";
import { fetchMock } from "./setup";
import { remotionServer, seed } from "./remotion-server";
import { remotionJob } from "./remotion-fixtures";
import * as api from "@/features/remotion_templates/api";
import * as local from "@/features/remotion_templates/localSessions";

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
  await seed("已有模板");
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
  let initialSend: boolean | undefined;
  act(() => {
    initialSend = result.current.send("静态标题");
  });
  expect(initialSend).toBe(true);
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  const code = result.current.code;
  let dirtySend: boolean | undefined;
  act(() => {
    result.current.change("size", 80);
    result.current.change("size", 96);
    dirtySend = result.current.send("不能发出的修改");
  });
  expect(dirtySend).toBe(false);
  expect(result.current.values.size).toBe(96);
  expect(edits).toHaveLength(0);
  let busySend: boolean | undefined;
  act(() => {
    result.current.saveParameters();
    busySend = result.current.send("保存期间不能发出的修改");
    result.current.saveParameters();
    result.current.change("size", 120);
  });
  expect(busySend).toBe(false);
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
  const cursor = fake.emitted.at(-1)!.id;
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
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST" && !String(call[0]).endsWith("/ack")),
  ).toHaveLength(1);
  unmount();
  expect(fake.streams.size).toBe(0);
});

// 同一轮直接调用 hook 也必须阻止恢复与待提交参数竞争，不能只依赖按钮 disabled。
test("hook 在参数待提交及读取期间拒绝重复恢复", async () => {
  let release: (() => void) | undefined;
  let holdRead = false;
  const real = local.snapshot;
  const snapshots = spyOn(local, "snapshot").mockImplementation((work, before) =>
    holdRead
      ? new Promise((resolve) => {
          release = () => resolve(real(work, before));
        })
      : real(work, before),
  );
  const fake = remotionServer((path) => {
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
  /** 到目前为止从本地会话库读取快照的次数。 */
  const reads = () => snapshots.mock.calls.length;
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
  await act(async () => release!());
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
    await seed("原会话");
    await seed("目标会话");
    const { result } = renderHook(() => useTemplateSession(() => {}));
    act(() => result.current.select("work-1"));
    await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
    act(() => {
      result.current.change("size", 80);
      result.current.select("work-1");
    });
    expect(result.current.navigation).toBeNull();
    act(() => result.current.select(target));
    expect(result.current.navigation).toEqual({ work: target, saving: false });
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
    // 每次提交都是新的任务 ID，与服务端一致。
    let saves = 0;
    const fake = responses(() =>
      Response.json({ ...remotionJob("running"), id: ++saves === 1 ? "saving" : "saving-2" }),
    );
    await seed("原会话");
    await seed("目标会话");
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
        id: "saving-2",
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

// 服务端在入队前以 503 说明原因（如缺少 Linux 沙箱）时，界面显示该原因，且不提示检查历史会话。
test("创建被 503 拒绝时显示服务端原因而非通用提示", async () => {
  const reason = "校验环境不可用：the isolated validator runs only on Linux, this host is darwin。";
  remotionServer((path, options) =>
    options?.method === "POST" && path.endsWith("/works")
      ? Response.json({ detail: reason }, { status: 503 })
      : undefined,
  );
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("新的标题"));
  await waitFor(() => expect(result.current.error).toBe(reason));
  expect(result.current.busy).toBeNull();
  expect(result.current.error).not.toContain("历史会话");
});

// 没有可用文本 detail 的 503 仍显示通用就绪提示；入队前被拒绝，同样不提示检查历史会话。
test.each([null, { detail: ["not", "text"] }, { detail: "" }])(
  "缺少文本 detail 的 503 回退到通用提示：%j",
  async (body) => {
    remotionServer((path, options) =>
      options?.method === "POST" && path.endsWith("/works")
        ? body === null
          ? new Response(null, { status: 503 })
          : Response.json(body, { status: 503 })
        : undefined,
    );
    const { result } = renderHook(() => useTemplateSession(() => {}));
    act(() => result.current.send("新的标题"));
    await waitFor(() => expect(result.current.error).toBe("服务尚未就绪，请检查服务端配置。"));
  },
);
