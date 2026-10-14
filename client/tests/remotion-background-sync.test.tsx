/**
 * 后台会话同步回归：未在 SSE 中打开、仍在排队或运行的本地会话通过分页事件接口补齐，写入本地后确认送达并刷新侧栏；
 * 打开的会话留给 SSE，服务端 404 标记为可重试删除。网络使用协议替身，存储使用内存实现，执行 bun run test。
 */
import { expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { syncBackground, SYNC_INTERVAL_MS } from "@/features/remotion_templates/backgroundSync";
import * as local from "@/features/remotion_templates/localSessions";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { remotionJob } from "./remotion-fixtures";
import { remotionServer, seed } from "./remotion-server";
import { fetchMock } from "./setup";

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
