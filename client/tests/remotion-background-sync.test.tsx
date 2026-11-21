/**
 * 后台会话同步回归：未在 SSE 中打开、仍在排队或运行的本地会话通过分页事件接口补齐，写入本地后确认送达并刷新侧栏；
 * 打开的会话留给 SSE，服务端 404 标记为可重试删除。网络使用协议替身，存储使用内存实现，执行 bun run test。
 */
import { expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { syncBackground, SYNC_INTERVAL_MS } from "@/features/remotion_templates/backgroundSync";
import * as local from "@/features/remotion_templates/localSessions";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { remotionJob } from "./remotion-fixtures";
import { remotionServer, seed } from "./remotion-server";
import { fetchMock, mockDesktop } from "./setup";
import { checkUpdate, downloadUpdate, initializeUpdates, resetUpdates, tryApplyUpdate } from "@/features/updates/store";
import { updateBlockers, useUpdateBlocker } from "@/lib/update-guard";
import { registrationJournal } from "./remotion-registration-fixture";

/** 协议替身：新作品的任务停在运行中，之后由测试推进。 */
function runningServer(overrides: Parameters<typeof remotionServer>[0] = () => undefined) {
  return remotionServer((path, options, url) =>
    path === "/works" && options?.method === "POST"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : overrides(path, options, url),
  );
}
/** 某个会话的分页事件请求。 */
function eventReads(work = "work-1") {
  return fetchMock.mock.calls.filter((call) => String(call[0]).includes(`/works/${work}/events?`));
}

// 后台运行的会话已在服务端完成：挂载时分页拉取全部待送达事件写入本地、确认送达，侧栏不再停在“正在处理”。
test("挂载时同步后台会话并刷新侧栏", async () => {
  const fake = runningServer();
  await seed("后台会话");
  await act(async () => fake.advance(remotionJob("succeeded", "version-1")));
  const last = fake.emitted.at(-1)!.id;
  render(<RemotionWorkspace />);
  await waitFor(() => expect(screen.getByRole("button", { name: /后台会话/ }).textContent).toContain("已完成"));
  const snapshot = await local.snapshot("work-1");
  expect(snapshot.cursor).toBe(last);
  expect(snapshot.job.status).toBe("succeeded");
  expect(snapshot.work.current_version_id).toBe("version-1");
  expect(snapshot.messages.at(-1)?.text).toContain("模板已就绪");
  // 替身每页两条，三条新事件需要读取到取空为止；确认后服务端不再保留。
  expect(eventReads().length).toBeGreaterThanOrEqual(3);
  expect(fake.acks.get("work-1")?.at(-1)).toBe(last);
  expect(fake.records.some((event) => event.work_id === "work-1" && event.id <= last)).toBe(false);
  for (const [, options] of eventReads())
    expect((options?.headers as Record<string, string>)["X-IMV-Device"]).toBe(await local.deviceId());
  expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/stream?"))).toBe(false);
});

// 挂载后才完成的后台任务由每 30 秒一次的同步补齐；已结束的会话不再请求事件。
test("定时同步更新后台会话状态", async () => {
  const intervals = spyOn(window, "setInterval");
  const fake = runningServer();
  await seed("定时会话");
  render(<RemotionWorkspace />);
  await waitFor(() => expect(eventReads()).toHaveLength(1));
  expect(screen.getByRole("button", { name: /定时会话/ }).textContent).toContain("正在处理");
  const tick = intervals.mock.calls.find((call) => call[1] === SYNC_INTERVAL_MS)![0] as () => void;
  await act(async () => fake.advance(remotionJob("failed")));
  await act(async () => tick());
  await waitFor(() => expect(screen.getByRole("button", { name: /定时会话/ }).textContent).toContain("待重试"));
  const reads = eventReads().length;
  await act(async () => tick());
  expect(eventReads()).toHaveLength(reads);
});

// SSE 正在订阅的会话不经后台同步；同步途中会话被打开时，已取到的事件留给 SSE，不写入也不确认。
test("打开的会话交给 SSE，不被后台同步写入", async () => {
  let opened = false;
  const fake = runningServer((path) => {
    if (path.endsWith("/events")) opened = true;
    return undefined;
  });
  await seed("打开中");
  await act(async () => fake.advance(remotionJob("succeeded", "version-1")));
  const before = (await local.snapshot("work-1")).cursor;
  expect(await syncBackground(() => opened, new AbortController().signal)).toBe(false);
  expect(eventReads()).toHaveLength(1);
  expect((await local.snapshot("work-1")).cursor).toBe(before);
  expect(fake.acks.has("work-1")).toBe(false);
  // 打开后的定时同步直接跳过该会话。
  expect(await syncBackground(() => true, new AbortController().signal)).toBe(false);
  expect(eventReads()).toHaveLength(1);
});

// 后台会话在服务端已删除（404）时与打开中的会话一样标记为可重试删除，之后不再同步。
test("后台会话已被服务端删除时标记为可重试删除", async () => {
  const fake = runningServer();
  await seed("已清理会话");
  fake.works.delete("work-1");
  render(<RemotionWorkspace />);
  const row = await screen.findByRole("button", { name: /可重试删除/ });
  expect(row.textContent).toContain("已清理会话");
  expect(row.hasAttribute("disabled")).toBe(true);
  expect(await local.pendingWorks()).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "删除聊天" }));
  expect(screen.getByRole("button", { name: "重试删除" })).toBeTruthy();
});

// 终态事件已被服务端过期清理（客户端长时间离线）：本地仍停在运行中，同步改读任务当前状态校正，会话不再被锁定。
test("事件已过期时用任务状态校正停在运行中的后台会话", async () => {
  const fake = runningServer();
  await seed("过期会话");
  await act(async () => fake.advance(remotionJob("succeeded", "version-1")));
  fake.records.length = 0;
  render(<RemotionWorkspace />);
  await waitFor(() => expect(screen.getByRole("button", { name: /过期会话/ }).textContent).toContain("已完成"));
  const snapshot = await local.snapshot("work-1");
  expect(snapshot.job.status).toBe("succeeded");
  expect(snapshot.work.current_version_id).toBe("version-1");
  expect(await local.pendingWorks()).toEqual([]);
});

// 服务端任务仍在运行时不改本地记录；任务已不在服务端（404）时按会话已删除处理，网络失败留待下一轮。
test("核对任务状态：仍在运行不改动，404 标记删除，网络失败保持原状", async () => {
  let mode: "running" | "missing" | "offline" = "running";
  runningServer((path) => {
    if (!/^\/jobs\/[^/]+$/.test(path)) return undefined;
    if (mode === "missing") return new Response(null, { status: 404 });
    if (mode === "offline") throw new TypeError("offline");
    return undefined;
  });
  await seed("核对会话");
  const signal = new AbortController().signal;
  expect(await syncBackground(() => false, signal, true)).toBe(false);
  mode = "offline";
  expect(await syncBackground(() => false, signal, true)).toBe(false);
  expect((await local.pendingWorks()).map((item) => item.work.id)).toEqual(["work-1"]);
  mode = "missing";
  expect(await syncBackground(() => false, signal, true)).toBe(true);
  expect(await local.pendingWorks()).toEqual([]);
});

// 后续各轮同步不再读取任务状态：任务接口不被轮询。
test("只有挂载后的第一轮同步读取任务状态", async () => {
  runningServer();
  await seed("不轮询");
  const signal = new AbortController().signal;
  await syncBackground(() => false, signal);
  await syncBackground(() => false, signal);
  expect(fetchMock.mock.calls.some((call) => /\/jobs\/[^/]+$/.test(String(call[0])))).toBe(false);
});

// 控制首次落盘的时机，分别保留和关闭聊天；同一任务在重启期间完成，回来接收同一结果。
test.each(["保持打开", "关闭"])("聊天%s后任务已登记，自动整包更新并恢复原任务", async (chat) => {
  const fake = runningServer();
  const memory = local.memoryStorage();
  const saving = Promise.withResolvers<void>();
  let savingStarted = false;
  local.setStorage({ ...memory, async save(server, change) {
    savingStarted = true;
    await saving.promise;
    return memory.save(server, change);
  } });
  let applied = 0;
  let pendingReads = 0;
  const journal = registrationJournal();
  mockDesktop(async (command, args) => {
    if (command === "local_settings") return {};
    if (command === "remotion_sessions" && String(args.operation).startsWith("registrations_")) return journal(args);
    if (command === "remotion_sessions") { pendingReads++; throw new Error("旧版无法打开新版会话库"); }
    if (command !== "client_update") return null;
    if (args.operation === "status") return { enabled: true };
    if (args.operation === "check") return { version: "2.0.0", mode: "native" };
    if (args.operation === "apply") applied++;
    return null;
  });
  await initializeUpdates(); await checkUpdate();
  // 明确阻止更新计时器抢先应用，直到聊天关闭与所有前置断言完成。
  const gate = renderHook(({ held }) => useUpdateBlocker("等待测试完成聊天操作", held), { initialProps: { held: true } });
  const view = render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("字效描述"), { target: { value: "保留这条聊天" } });
  fireEvent.submit(screen.getByRole("button", { name: "发送" }).closest("form")!);
  await waitFor(() => expect(savingStarted).toBe(true));
  await act(async () => { await downloadUpdate(); });
  expect(updateBlockers()).toContain("正在完成保存、上传或读取请求");
  expect(applied).toBe(0);
  await act(async () => { saving.resolve(); });
  await waitFor(() => expect(fake.streams.size).toBe(1));
  expect(updateBlockers()).toEqual(["等待测试完成聊天操作"]);
  const before = await local.snapshot("work-1");
  expect(applied).toBe(0);
  if (chat === "关闭") {
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    await waitFor(() => expect(fake.streams.size).toBe(0));
  }
  expect(applied).toBe(0);
  gate.rerender({ held: false });
  await waitFor(() => expect(applied).toBe(1), { timeout: 3000 });
  expect(pendingReads).toBe(0);
  view.unmount();
  // 新进程重新挂载，服务端在客户端离线期间完成，消息与任务均从已有记录续接。
  fake.advance(remotionJob("succeeded", "version-1"));
  resetUpdates();
  render(<RemotionWorkspace />);
  await waitFor(() => expect(screen.getByRole("button", { name: /保留这条聊天/ }).textContent).toContain("已完成"));
  const after = await local.snapshot("work-1");
  expect(after.job.id).toBe(before.job.id);
  expect(after.work.current_version_id).toBe("version-1");
  expect(after.cursor).toBeGreaterThan(before.cursor);
  expect(fetchMock.mock.calls.filter(([url, options]) => String(url).endsWith("/works") && options?.method === "POST")).toHaveLength(1);
  expect(fetchMock.mock.calls.some(([url, options]) => String(url).endsWith("/cancel") || options?.method === "DELETE")).toBe(false);
});

// 服务端已接受续写但数据库无法登记：关闭聊天或重启后仍阻止整包更新，只重试本地登记，不重发任务。
test("登记失败保留持久回执，恢复同一任务后才允许整包更新", async () => {
  const journal = registrationJournal();
  const operations: unknown[] = [];
  mockDesktop(async (command, args) => {
    if (command === "remotion_sessions" && String(args.operation).startsWith("registrations_")) return journal(args);
    if (command === "local_settings") return {};
    if (command !== "client_update") return null;
    operations.push(args.operation);
    if (args.operation === "status") return { enabled: true };
    if (args.operation === "check") return { version: "2.0.0", mode: "native" };
    return null;
  });
  remotionServer(path => path.endsWith("/messages")
    ? Response.json({ ...remotionJob("running"), id: "job-2" }) : undefined);
  await seed("写入失败仍要保留");
  const memory = local.storage();
  const session = renderHook(() => useTemplateSession(() => {}));
  act(() => session.result.current.select("work-1"));
  await waitFor(() => expect(session.result.current.connection).toBe("live"));
  local.setStorage({ ...memory, save: async () => { throw new local.LocalStoreError("磁盘写入失败"); } });
  act(() => { session.result.current.send("继续制作"); });
  await waitFor(() => expect(session.result.current.error).toContain("磁盘写入失败"));
  expect((await local.registrationBlockers()).join()).toContain("job-2");
  expect((await memory.work(local.server(), "work-1"))?.job.id).toBe("job-1");
  session.unmount();
  // Windows 重启更换页面端口后无法读取原 localStorage，原生回执仍必须阻止更新。
  localStorage.clear();
  local.setStorage({ ...memory, save: async () => { throw new local.LocalStoreError("磁盘写入失败"); } });
  expect((await local.registrationBlockers()).join()).toContain("job-2");
  await initializeUpdates(); await checkUpdate(); await downloadUpdate();
  await tryApplyUpdate();
  expect(operations).not.toContain("download");
  expect(operations).not.toContain("apply");
  local.setStorage(memory);
  expect((await local.history())[0].job.id).toBe("job-2");
  expect(await local.registrationBlockers()).toEqual([]);
  await tryApplyUpdate();
  await waitFor(() => expect(operations.filter(op => op === "apply")).toHaveLength(1));
  expect(fetchMock.mock.calls.filter(([url, options]) => String(url).endsWith("/messages") && options?.method === "POST")).toHaveLength(1);
});
