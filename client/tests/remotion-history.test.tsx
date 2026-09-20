/** 公开聊天历史及 SSE 会话恢复测试；隔离 HTTP、存储和 iframe，执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ChatPanel } from "@/features/remotion_templates/ChatPanel";
import type { ChatMessage } from "@/features/remotion_templates/model";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { useWorkHistory } from "@/features/remotion_templates/useWorkHistory";
import * as api from "@/features/remotion_templates/api";
import { remotionJob } from "./remotion-fixtures";
import { latestCopy } from "./remotion-version-helpers";
import { remotionServer } from "./remotion-server";
import { fetchMock } from "./setup";

// 侧栏选择会话恢复各自消息与版本，新增只清空当前视图，历史会话和任务时间保留。
test("历史侧栏切换、任务耗时和新增独立视图", async () => {
  const fake = remotionServer();
  await api.create("春日标题");
  await api.create("夏日标题");
  render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: /春日标题/ }));
  await waitFor(() =>
    expect(
      screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
    ).toContain("version-1"),
  );
  expect(within(screen.getByRole("log")).queryByText("春日标题") !== null).toBe(
    true,
  );
  expect(screen.getByText(/耗时 68 秒/).textContent).toContain("已完成");
  fireEvent.click(screen.getByRole("button", { name: /夏日标题/ }));
  await waitFor(() =>
    expect(
      screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
    ).toContain("version-2"),
  );
  expect(within(screen.getByRole("log")).queryByText("春日标题") === null).toBe(
    true,
  );
  expect(within(screen.getByRole("log")).queryByText("夏日标题") !== null).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "新增聊天" }));
  await waitFor(() => expect(fake.streams.size).toBe(0));
  expect(within(screen.getByRole("log")).queryByText("夏日标题") === null).toBe(
    true,
  );
  expect(
    screen.getByRole("button", { name: /夏日标题/ }).textContent,
  ).toContain("已完成");
  expect(
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/cancel")),
  ).toHaveLength(0);
});

// 页面重新挂载从服务端恢复当前 ID；后台完成后读取新结果，不依赖上次内存消息或倒计时。
test("刷新恢复历史图片、成功版本和服务端时间", async () => {
  const fake = remotionServer();
  await api.create("图片历史", "asset-1");
  fake.snapshots.get("work-1")!.messages[0].reconstructed = true;
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  const view = render(<RemotionWorkspace />);
  const image = await screen.findByAltText<HTMLImageElement>("历史参考图片");
  expect(image.src).toBe(api.apiUrl("/assets/asset-1"));
  expect(screen.getAllByText(/历史恢复/).length).toBe(1);
  await waitFor(() => expect(fake.streams.size).toBe(1));
  view.unmount();
  await waitFor(() => expect(fake.streams.size).toBe(0));
  render(<RemotionWorkspace />);
  await waitFor(() =>
    expect(latestCopy().hasAttribute("disabled")).toBe(false),
  );
  expect(screen.getByText(/耗时 68 秒/).textContent).toContain("已完成");
  expect(localStorage.length).toBe(1);
  expect(localStorage.getItem(localStorage.key(0)!)).toBe("work-1");
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
});

// SSE 失联后重放成功消息，重复 ID 不重复追加，也不产生第二次创建或任务轮询。
test("SSE 断线续传、重复事件去重且不重发任务", async () => {
  const fake = remotionServer((path) =>
    path === "/works"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : undefined,
  );
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("断线恢复"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  const cursor = fake.snapshots.get("work-1")!.cursor;
  await act(async () => {
    for (const stream of fake.streams) {
      fake.streams.delete(stream);
      stream.controller.error(new TypeError("offline"));
    }
  });
  expect(result.current.connection).toBe("reconnecting");
  fake.advance(remotionJob());
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"), {
    timeout: 3500,
  });
  expect(result.current.messages).toHaveLength(2);
  const record = fake.records.at(-1)!;
  await act(async () => {
    for (const stream of fake.streams)
      stream.controller.enqueue(
        new TextEncoder().encode(
          `id: ${record.id}\nevent: ${record.type}\ndata: ${JSON.stringify(record)}\n\n`,
        ),
      );
  });
  expect(result.current.messages).toHaveLength(2);
  const connections = fetchMock.mock.calls.filter((call) =>
    String(call[0]).includes("/stream?"),
  );
  expect(connections).toHaveLength(2);
  expect(connections[1][1]?.headers).toHaveProperty(
    "Last-Event-ID",
    String(cursor),
  );
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
  expect(
    fetchMock.mock.calls.some((call) => /\/jobs\/[^/]+$/.test(String(call[0]))),
  ).toBe(false);
});

// 旧数据库游标失效后重新取快照，恢复期间不会使用旧代码或发送重复修改。
test("游标失效重新读取快照并恢复订阅", async () => {
  let connects = 0;
  const fake = remotionServer((path) =>
    path.endsWith("/stream") && ++connects === 1
      ? new Response(null, { status: 409 })
      : undefined,
  );
  await api.create("游标恢复");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(connects).toBe(2), { timeout: 3500 });
  expect(result.current.version?.id).toBe("version-1");
  expect(result.current.messages).toHaveLength(2);
  expect(fake.streams.size).toBe(1);
  expect(
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/session")),
  ).toHaveLength(2);
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
});

// 切换时仍在传输的旧成功版本不能回填新会话；任务不因读取中断被取消。
test.each([200, 404])("切换隔离迟到的版本下载：%s", async (status) => {
  let release: ((response: Response) => void) | undefined;
  const fake = remotionServer((path) =>
    path === "/versions/version-1/artifacts/Export.tsx"
      ? new Promise((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await api.create("慢会话");
  await api.create("快会话");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(release).toBeDefined());
  act(() => result.current.select("work-2"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  await act(async () => release!(new Response("old code", { status })));
  expect(result.current.workId).toBe("work-2");
  expect(result.current.code).not.toContain("old code");
  expect(result.current.error).toBe("");
  expect(fake.snapshots.has("work-1")).toBe(true);
  expect(
    fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/cancel")),
  ).toBe(false);
});

// 列表分页重叠去重；加载失败保留已有列表，聚焦和手动刷新均只触发读取。
test("历史分页、失败保留与刷新", async () => {
  const fake = remotionServer();
  await api.create("会话一");
  await api.create("会话二");
  const [one, two] = [...fake.summaries.values()];
  let failed = false;
  fetchMock.mockImplementation(
    Object.assign(
      async (input: RequestInfo | URL) => {
        if (failed) throw new TypeError("offline");
        return Response.json(
          String(input).includes("cursor=")
            ? { items: [one, two], next_cursor: null }
            : { items: [one], next_cursor: "page-two" },
        );
      },
      { preconnect: () => {} },
    ),
  );
  const { result } = renderHook(useWorkHistory);
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  act(() => result.current.more());
  await waitFor(() => expect(result.current.items).toHaveLength(2));
  failed = true;
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toContain("无法连接"));
  expect(result.current.items).toHaveLength(2);
  failed = false;
  act(() => window.dispatchEvent(new Event("focus")));
  await waitFor(() => expect(result.current.error).toBe(""));
  expect(result.current.items).toHaveLength(1);
});

// 较早消息请求晚于新事件返回时只合并消息，不能回退当前任务和成功版本。
test("更早消息分页去重且不覆盖新任务状态", async () => {
  let release: ((response: Response) => void) | undefined;
  const fake = remotionServer((path, _options, url) =>
    path.endsWith("/session") && url?.searchParams.has("before")
      ? new Promise((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await api.create("最近消息");
  const snap = fake.snapshots.get("work-1")!;
  snap.messages = snap.messages.map((message, index) => ({
    ...message,
    sequence: 20 + index,
  }));
  snap.next_before = 20;
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  act(() => {
    void result.current.older();
  });
  await waitFor(() => expect(release).toBeDefined());
  await act(async () =>
    fake.advance({ ...remotionJob("running"), id: "new-job" }),
  );
  await act(async () =>
    release!(
      Response.json({
        ...snap,
        job: { ...snap.job, status: "failed" },
        work: { id: "work-1", current_version_id: null },
        messages: [
          { id: "older", sequence: 1, role: "user", text: "更早消息" },
          snap.messages[0],
        ],
        next_before: null,
      }),
    ),
  );
  await waitFor(() => expect(result.current.olderLoading).toBe(false));
  expect(result.current.messages.map((message) => message.sequence)).toEqual([
    1, 20, 21,
  ]);
  expect(result.current.nextBefore).toBeNull();
  expect(result.current.job?.id).toBe("new-job");
  expect(result.current.job?.status).toBe("running");
  expect(result.current.version?.id).toBe("version-1");
});

// 只有用户明确停止才发出取消请求，终态回执恢复可操作状态，卸载不再取消。
test("明确停止任务后恢复公开快照", async () => {
  remotionServer((path) => {
    if (path === "/works")
      return Response.json({
        work: { id: "work-1" },
        job: remotionJob("running"),
      });
    if (path.endsWith("/cancel"))
      return Response.json(remotionJob("cancelled"));
  });
  const view = renderHook(() => useTemplateSession(() => {}));
  act(() => view.result.current.send("停止测试"));
  await waitFor(() => expect(view.result.current.connection).toBe("live"));
  await act(async () => view.result.current.stop());
  expect(view.result.current.job?.status).toBe("cancelled");
  expect(view.result.current.busy).toBeNull();
  expect(view.result.current.messages).toHaveLength(2);
  view.unmount();
  expect(
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/cancel")),
  ).toHaveLength(1);
});

// 已删除或失效的会话直接回到空白页，不提供反复读取不存在会话的恢复入口。
test("失效会话 ID 自动清空并允许新增", async () => {
  remotionServer();
  localStorage.setItem(
    `imv.remotion.selected:${api.apiUrl("")}`,
    "missing-work",
  );
  render(<RemotionWorkspace />);
  await screen.findByText(/会话已删除或正在清理/);
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "新的任务" },
  });
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(localStorage.length).toBe(0);
  expect(screen.queryByRole("button", { name: "刷新任务" }) === null).toBe(
    true,
  );
});

// 加载更早消息时保留阅读位置；模拟滚动原语的位移，新增末尾消息仍滚动到最新处。
test("加载更早消息不会把阅读位置拉回底部", () => {
  const original = HTMLElement.prototype.scrollIntoView;
  if (!original) HTMLElement.prototype.scrollIntoView = () => {};
  const scroll = spyOn(
    HTMLElement.prototype,
    "scrollIntoView",
  ).mockImplementation(function (this: HTMLElement) {
    const log = this.closest<HTMLElement>('[role="log"]');
    if (log) log.scrollTop = 1000;
  });
  const messages: ChatMessage[] = [
    { id: "recent", sequence: 20, role: "user", text: "当前消息" },
  ];
  const props = {
    busy: false,
    disabled: false,
    canStop: false,
    first: false,
    onSend: () => {},
    onStop: () => {},
  };
  try {
    const view = render(<ChatPanel {...props} messages={messages} />);
    const log = screen.getByRole("log");
    log.scrollTop = 125;
    const older: ChatMessage = {
      id: "older",
      sequence: 1,
      role: "user",
      text: "更早消息",
    };
    view.rerender(<ChatPanel {...props} messages={[older, ...messages]} />);
    expect(log.scrollTop).toBe(125);
    expect(within(log).getByText("更早消息").textContent).toBe("更早消息");
    view.rerender(
      <ChatPanel
        {...props}
        messages={[
          older,
          ...messages,
          { id: "latest", sequence: 21, role: "assistant", text: "新回复" },
        ]}
      />,
    );
    expect(log.scrollTop).toBe(1000);
  } finally {
    scroll.mockRestore();
    if (!original)
      Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});
