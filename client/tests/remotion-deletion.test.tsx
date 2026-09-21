/** 字效整条删除回归：真实会话 hook/历史 UI/SSE，网络与播放器隔离；执行 bun run test。 */
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
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { useWorkHistory } from "@/features/remotion_templates/useWorkHistory";
import { remotionServer } from "./remotion-server";
import { fetchMock } from "./setup";

/** 统计删除写入，排除历史刷新和断线恢复读取。 */
function deletes() {
  return fetchMock.mock.calls.filter(
    ([, options]) => options?.method === "DELETE",
  );
}

// 当前会话确认前不写入，确认后清空播放器和本地选择，历史只剩其他会话。
test("删除当前聊天先确认，成功清理当前视图和订阅", async () => {
  const fake = remotionServer();
  await api.create("待删除");
  await api.create("保留会话");
  render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: /待删除/ }));
  await waitFor(() => expect(fake.streams.size).toBe(1));
  fireEvent.click(screen.getByTitle("删除「待删除」"));
  expect(deletes()).toHaveLength(0);
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }),
  );
  expect(deletes()).toHaveLength(0);
  expect(fake.snapshots.has("work-1")).toBe(true);
  fireEvent.click(screen.getByTitle("删除「待删除」"));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog") === null).toBe(true));
  expect(screen.queryByTitle("Remotion 字效播放器") === null).toBe(true);
  expect(screen.queryByTitle("删除「待删除」") === null).toBe(true);
  expect(screen.getByRole("button", { name: /保留会话/ }) !== null).toBe(true);
  expect(fake.streams.size).toBe(0);
  expect(
    localStorage.getItem(`imv.remotion.selected:${api.apiUrl("")}`),
  ).toBeNull();
  expect(deletes()).toHaveLength(1);
});

// 删除其他会话保留当前未保存参数、成功版本及同一条 SSE；失败不会自动重发。
test("其他聊天删除失败可重试且不打断当前草稿", async () => {
  let fails = true;
  const fake = remotionServer((_, options) =>
    options?.method === "DELETE" && fails
      ? new Response(null, { status: 503 })
      : undefined,
  );
  await api.create("正在编辑");
  await api.create("其他聊天");
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
  expect(fake.snapshots.has("work-2")).toBe(false);
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
  await api.create("草稿删除");
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
    fetchMock.mock.calls.filter(([, o]) => o?.method === "POST"),
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
  await api.create("慢删除");
  render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByTitle("删除「慢删除」"));
  const confirm = screen.getByRole("button", { name: "确认删除" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await waitFor(() => expect(deletes()).toHaveLength(1));
  expect(
    screen
      .getByRole("button", { name: "正在停止并清理…" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await act(async () => release(new Response(null, { status: 503 })));
  expect(
    (await screen.findByRole("button", { name: "重试删除" })) !== null,
  ).toBe(true);
  expect(
    within(screen.getByRole("dialog")).getByRole("alert").textContent,
  ).toContain("未确认完成");
  expect(deletes()).toHaveLength(1);
});

// 其他窗口删除会终止当前连接；没有永久保留聊天或反复重连、自动重建会话。
test("已打开 SSE 收到删除控制信号后清空会话", async () => {
  const fake = remotionServer();
  await api.create("远端删除");
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

// 恢复不存在或清理中的会话，不留下刷新任务和循环重连入口。
test.each([404, 410])("会话恢复返回 %s 时移除本地选择", async (status) => {
  remotionServer((path) =>
    path.endsWith("/session") ? new Response(null, { status }) : undefined,
  );
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "missing");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.error).toContain("已删除"));
  expect(result.current.workId).toBeNull();
  expect(result.current.retryMode).toBeNull();
  expect(localStorage.length).toBe(0);
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
  await api.create("慢版本");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(release).toBeDefined());
  await act(async () => result.current.deleteWork("work-1"));
  await act(async () => release(new Response("old code")));
  expect(result.current.workId).toBeNull();
  expect(result.current.code).toBe("");
  expect(result.current.version).toBeNull();
});

// 删除完成会中断旧历史读取，迟到的分页不能把删除项重新插入列表。
test("历史删除淘汰迟到分页响应", async () => {
  const fake = remotionServer();
  await api.create("列表条目");
  let release!: (page: Awaited<ReturnType<typeof api.history>>) => void;
  const stale = { items: [...fake.summaries.values()], next_cursor: null };
  const history = spyOn(api, "history")
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    )
    .mockResolvedValue({ items: [], next_cursor: null });
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
  await api.create("卸载清理");
  const view = render(<RemotionWorkspace />);
  fireEvent.click(await screen.findByTitle("删除「卸载清理」"));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(release).toBeDefined());
  view.unmount();
  const before = fetchMock.mock.calls.length;
  await act(async () => release(new Response(null, { status: 204 })));
  expect(fetchMock.mock.calls.length).toBe(before);
});
