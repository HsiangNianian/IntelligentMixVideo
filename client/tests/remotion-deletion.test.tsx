/** 字效整条删除回归：真实会话 hook/历史 UI/SSE，服务端删除后同时清除本地会话库；网络与播放器隔离，执行 bun run test。 */
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
import * as api from "@/features/remotion_templates/api";
import * as local from "@/features/remotion_templates/localSessions";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { useWorkHistory } from "@/features/remotion_templates/useWorkHistory";
import { remotionServer, seed } from "./remotion-server";
import { fetchMock } from "./setup";

/** 历史行内的删除按钮：删除提示改为悬停 Tooltip，按会话标题定位所在行再取删除按钮。 */
function deleteButton(title: string) {
  const row = screen.getByRole("button", { name: new RegExp(title) }).parentElement!;
  return within(row).getByRole("button", { name: "删除聊天" });
}

/** 统计删除写入，排除历史刷新和断线恢复读取。 */
function deletes() {
  return fetchMock.mock.calls.filter(
    ([, options]) => options?.method === "DELETE",
  );
}

// 当前会话确认前不写入，确认后清空播放器和本地选择，历史只剩其他会话。
test("删除当前聊天先确认，成功清理当前视图和订阅", async () => {
  const fake = remotionServer();
  await seed("待删除");
  await seed("保留会话");
  render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: /待删除/ }));
  await waitFor(() => expect(fake.streams.size).toBe(1));
  fireEvent.click(deleteButton("待删除"));
  expect(deletes()).toHaveLength(0);
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }),
  );
  expect(deletes()).toHaveLength(0);
  expect(fake.works.has("work-1")).toBe(true);
  fireEvent.click(deleteButton("待删除"));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog") === null).toBe(true));
  expect(screen.queryByTitle("Remotion 字效播放器") === null).toBe(true);
  expect(screen.queryByRole("button", { name: /待删除/ }) === null).toBe(true);
  expect(screen.getByRole("button", { name: /保留会话/ }) !== null).toBe(true);
  expect(fake.streams.size).toBe(0);
  expect(
    localStorage.getItem(`imv.remotion.selected:${api.apiUrl("")}`),
  ).toBeNull();
  expect(deletes()).toHaveLength(1);
  // 服务端与本机会话库都不再保留该会话，另一条会话不受影响。
  expect(fake.works.has("work-1")).toBe(false);
  expect(await local.storage().work(local.server(), "work-1")).toBeNull();
  expect(await local.storage().work(local.server(), "work-2")).not.toBeNull();
});

// 删除其他会话保留当前未保存参数、成功版本及同一条 SSE；失败不会自动重发。
test("其他聊天删除失败可重试且不打断当前草稿", async () => {
  let fails = true;
  const fake = remotionServer((_, options) =>
    options?.method === "DELETE" && fails
      ? new Response(null, { status: 503 })
      : undefined,
  );
  await seed("正在编辑");
  await seed("其他聊天");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  act(() => result.current.change("size", 105));
  const stream = [...fake.streams][0];
  await act(async () => {
    await expect(result.current.deleteWork("work-2")).rejects.toThrow();
  });
  expect(result.current.values.size).toBe(105);
  expect(result.current.dirty).toBe(true);
  expect(result.current.deleting).toBe(false);
  expect(fake.streams.has(stream)).toBe(true);
  expect(deletes()).toHaveLength(1);
  fails = false;
  await act(async () => result.current.deleteWork("work-2"));
  expect(fake.works.has("work-2")).toBe(false);
  expect(result.current.workId).toBe("work-1");
  expect(result.current.values.size).toBe(105);
  expect(fake.streams.has(stream)).toBe(true);
});

// 删除当前会话已获放弃草稿授权；服务器结果未知时锁定编辑，显式重试后清空。
test("当前删除失败锁定写入，重试删除不保存本地参数", async () => {
  let fails = true;
  remotionServer((_, options) =>
    options?.method === "DELETE" && fails
      ? new Response(null, { status: 503 })
      : undefined,
  );
  await seed("草稿删除");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  act(() => result.current.change("size", 105));
  await act(async () => {
    await expect(result.current.deleteWork("work-1")).rejects.toThrow();
  });
  expect(result.current.deleting).toBe(true);
  act(() => {
    result.current.send("不能发送");
    result.current.saveParameters();
    result.current.retry();
    result.current.change("size", 110);
  });
  expect(result.current.values.size).toBe(105);
  expect(
    fetchMock.mock.calls.filter(([url, o]) => o?.method === "POST" && !String(url).endsWith("/ack")),
  ).toHaveLength(1);
  fails = false;
  await act(async () => result.current.deleteWork("work-1"));
  expect(result.current.workId).toBeNull();
  expect(result.current.version).toBeNull();
  expect(result.current.navigation).toBeNull();
});

// 双击确认只有一个写请求，失败留在对话框，不允许误以为已删除成功。
test("删除弹窗防重复提交并保留失败重试入口", async () => {
  let release!: (response: Response) => void;
  remotionServer((_, options) =>
    options?.method === "DELETE"
      ? new Promise((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await seed("慢删除");
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: /慢删除/ });
  fireEvent.click(deleteButton("慢删除"));
  const confirm = screen.getByRole("button", { name: "确认删除" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await waitFor(() => expect(deletes()).toHaveLength(1));
  expect(
    screen
      .getByRole("button", { name: "正在停止并清理…" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await act(async () =>
    release(
      new Response(JSON.stringify({ detail: "会话数据清理未完成，请重试删除。" }), {
        status: 503,
      }),
    ),
  );
  expect(
    (await screen.findByRole("button", { name: "重试删除" })) !== null,
  ).toBe(true);
  // 提示带上服务端说明，用户反馈的截图能看出是清理失败、超时还是连不上服务。
  const alert = within(screen.getByRole("dialog")).getByRole("alert").textContent;
  expect(alert).toContain("未确认完成");
  expect(alert).toContain("会话数据清理未完成，请重试删除。");
  expect(deletes()).toHaveLength(1);
});

// 删除要等服务端停止任务并清理对象存储，远程部署时比普通请求慢；不能套用 20 秒的普通超时而误报失败。
test("删除会话使用较长的请求超时", async () => {
  remotionServer();
  await seed("远程删除");
  const timers = spyOn(window, "setTimeout");
  await api.deleteWork("work-1");
  expect(timers.mock.calls.map(([, delay]) => delay)).toContain(api.DELETE_TIMEOUT_MS);
  expect(api.DELETE_TIMEOUT_MS).toBeGreaterThanOrEqual(120_000);
  timers.mockRestore();
});

// 其他窗口删除会终止当前连接；没有永久保留聊天或反复重连、自动重建会话。
test("已打开 SSE 收到删除控制信号后清空会话", async () => {
  const fake = remotionServer();
  await seed("远端删除");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  await act(async () => fake.remove("work-1"));
  await waitFor(() => expect(result.current.workId).toBeNull());
  expect(result.current.error).toContain("已删除");
  expect(result.current.messages).toHaveLength(0);
  expect(fake.streams.size).toBe(0);
  expect(deletes()).toHaveLength(0);
});

// 服务端已删除或正在清理时，移除本地选择并提示，不留下刷新任务和循环重连入口。
test.each([404, 410] as const)("会话恢复失败（%s）时移除本地选择", async (status) => {
  remotionServer((path) =>
    path.endsWith("/stream") ? new Response(null, { status }) : undefined,
  );
  await seed("服务端已清理");
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.error).toContain("已删除"));
  expect(result.current.workId).toBeNull();
  expect(result.current.retryMode).toBeNull();
  expect(localStorage.length).toBe(0);
});

// 服务端报告会话已删除或正在清理时，本地记录留在侧栏并标明可重试删除；再次删除后服务端与本机都不再保留。
test("服务端已删除的会话在历史列表中可重试删除", async () => {
  const fake = remotionServer((path) =>
    path.endsWith("/stream") ? new Response(null, { status: 410 }) : undefined,
  );
  await seed("服务端清理中");
  render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: /服务端清理中/ }));
  expect((await screen.findByText(/会话已删除或正在清理/)).textContent).toContain("可在历史列表重试未完成的删除");
  const row = await screen.findByRole("button", { name: /可重试删除/ });
  expect(row.hasAttribute("disabled")).toBe(true);
  fireEvent.click(deleteButton("服务端清理中"));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "重试删除" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /服务端清理中/ }) === null).toBe(true));
  expect(deletes()).toHaveLength(1);
  expect(fake.works.has("work-1")).toBe(false);
  expect(await local.storage().work(local.server(), "work-1")).toBeNull();
});

// 删除时旧导出读取不能在新空白会话中恢复代码或版本。
test("删除隔离迟到版本读取", async () => {
  let release!: (response: Response) => void;
  remotionServer((path) =>
    path.endsWith("Export.tsx")
      ? new Promise((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await seed("慢版本");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(release).toBeDefined());
  await act(async () => result.current.deleteWork("work-1"));
  await act(async () => release(new Response("old code")));
  expect(result.current.workId).toBeNull();
  expect(result.current.code).toBe("");
  expect(result.current.version).toBeNull();
});

// 删除完成会中断旧历史读取，迟到的本地列表不能把删除项重新插入列表。
test("历史删除淘汰迟到列表结果", async () => {
  remotionServer();
  await seed("列表条目");
  let release!: (page: Awaited<ReturnType<typeof local.history>>) => void;
  const stale = await local.history();
  const history = spyOn(local, "history")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    )
    .mockResolvedValue([]);
  const { result } = renderHook(() => useWorkHistory());
  await waitFor(() => expect(release).toBeDefined());
  act(() => result.current.remove("work-1"));
  await act(async () => release(stale));
  expect(result.current.items).toHaveLength(0);
  expect(history).toHaveBeenCalledTimes(2);
});

// 删除请求等待时离开页面，后台回执不启动新的历史读取或重新建立订阅。
test("卸载后删除回执不再刷新历史", async () => {
  let release!: (response: Response) => void;
  remotionServer((_, options) =>
    options?.method === "DELETE"
      ? new Promise((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await seed("卸载清理");
  const view = render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: /卸载清理/ });
  fireEvent.click(deleteButton("卸载清理"));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(release).toBeDefined());
  view.unmount();
  const before = fetchMock.mock.calls.length;
  await act(async () => release(new Response(null, { status: 204 })));
  expect(fetchMock.mock.calls.length).toBe(before);
});
