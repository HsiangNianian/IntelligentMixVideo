/**
 * 字效本地会话库回归：事件合并规则（任务状态、轮次、游标、作品版本指针）、消息分页、写入本地后确认送达，
 * 以及 Remotion 请求和资产发布携带本机设备 ID；存储使用每例独立的内存实现，执行 bun run test。
 */
import { expect, spyOn, test } from "bun:test";
import { act, renderHook, waitFor } from "@testing-library/react";
import * as api from "@/features/remotion_templates/api";
import * as local from "@/features/remotion_templates/localSessions";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { publishSprite } from "@/features/sprites/api";
import { create, toBinary } from "@bufbuild/protobuf";
import { PublishSpriteResponseSchema, SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { LoopRound, SessionJob, WorkEvent } from "@/features/remotion_templates/model";
import { remotionJob } from "./remotion-fixtures";
import { remotionServer, seed } from "./remotion-server";
import { fetchMock } from "./setup";

const ROUND: LoopRound = { layer: "outer", turn: 1, calls: [{ tool: "tools.plan_execute", status: "pass" }] };

/** 一条服务端事件，编号与类型由调用方指定。 */
function event(id: number, payload: Pick<WorkEvent, "type" | "data">): WorkEvent {
  return { id, work_id: "work-1", created_at: `2026-09-14T08:00:${String(id).padStart(2, "0")}Z`, ...payload } as WorkEvent;
}
/** 运行中的公开任务状态；服务端的状态事件不携带轮次。 */
function running(status: SessionJob["status"] = "running"): SessionJob {
  return { ...remotionJob(status), created_at: "2026-09-14T08:00:00Z", updated_at: "2026-09-14T08:00:00Z", parameters: null };
}
/** 登记一条只有初始任务的本地会话。 */
async function recordWork() {
  await local.record(
    { id: "work-1", current_version_id: null, created_at: "2026-09-14T08:00:00Z", request: { description: "本地标题" } },
    remotionJob("queued"),
  );
}

// 状态事件不含轮次时保留已写入的轮次；重复轮次只保存一次，编号不大于游标的重放被忽略。
test("本地合并保留轮次、去重并忽略已写入事件", async () => {
  await recordWork();
  await local.apply("work-1", [
    event(1, { type: "job.updated", data: running() }),
    event(2, { type: "job.round", data: { job_id: "job-1", round: ROUND } }),
    event(3, { type: "job.round", data: { job_id: "job-1", round: ROUND } }),
  ]);
  const { rounds: _omitted, ...ended } = { ...running("answered"), message: "已回答" };
  const stored = await local.apply("work-1", [
    event(2, { type: "job.round", data: { job_id: "job-1", round: { ...ROUND, turn: 9 } } }),
    event(4, { type: "job.updated", data: ended as SessionJob }),
  ]);
  expect(stored.cursor).toBe(4);
  const snapshot = await local.snapshot("work-1");
  expect(snapshot.job.status).toBe("answered");
  expect(snapshot.job.rounds).toEqual([ROUND]);
  expect(snapshot.cursor).toBe(4);
});

// 成功版本事件只更新作品指针（版本列表按需从服务端读取）；侧栏历史随之显示当前版本。
test("成功版本写入作品指针", async () => {
  await recordWork();
  await local.apply("work-1", [event(5, { type: "version.ready", data: { version_id: "version-9", job_id: "job-1" } })]);
  expect((await local.snapshot("work-1")).work.current_version_id).toBe("version-9");
  expect((await local.history()).items[0]).toMatchObject({ id: "work-1", current_version_id: "version-9", title: "本地标题" });
});

// 消息按序号分页：首屏返回最新一页与 next_before，继续向前读取到最早消息后不再有下一页。
test("本地消息分页返回 next_before", async () => {
  await recordWork();
  await local.apply(
    "work-1",
    [1, 2, 3].map((sequence) =>
      event(sequence, {
        type: "message.created",
        data: { id: `m-${sequence}`, sequence, job_id: "job-1", role: "user", text: `第 ${sequence} 条` },
      }),
    ),
  );
  const first = await local.snapshot("work-1", undefined, 2);
  expect(first.messages.map((message) => message.sequence)).toEqual([2, 3]);
  expect(first.next_before).toBe(2);
  const older = await local.snapshot("work-1", first.next_before!, 2);
  expect(older.messages.map((message) => message.sequence)).toEqual([1]);
  expect(older.next_before).toBeNull();
  await expect(local.snapshot("missing")).rejects.toMatchObject({ status: 404 });
});

// 每个 SSE 事件先写入本地会话库，再以该事件编号确认送达；重新连接时重新确认本地已有的游标。
test("事件写入本地后确认送达，重连时补发确认", async () => {
  const fake = remotionServer();
  await seed("确认送达");
  const order: string[] = [];
  const real = local.apply;
  spyOn(local, "apply").mockImplementation(async (work, events) => {
    const stored = await real(work, events);
    order.push(`apply:${events.at(-1)!.id}`);
    return stored;
  });
  const ack = spyOn(api, "ack").mockImplementation(async (_work, cursor) => {
    order.push(`ack:${cursor}`);
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  const cursor = (await local.snapshot("work-1")).cursor;
  // 打开连接时确认本地已有的游标，让服务端删除上次未确认成功的事件。
  await waitFor(() => expect(order).toContain(`ack:${cursor}`));
  await act(async () => fake.advance({ ...remotionJob("answered"), id: "job-2", message: "已回答" }));
  const last = fake.emitted.at(-1)!.id;
  await waitFor(() => expect(order).toContain(`ack:${last}`));
  expect(order.indexOf(`apply:${last}`)).toBeLessThan(order.indexOf(`ack:${last}`));
  expect((await local.snapshot("work-1")).cursor).toBe(last);
  ack.mockClear();
  await act(async () => {
    for (const stream of fake.streams) {
      fake.streams.delete(stream);
      stream.controller.error(new TypeError("offline"));
    }
  });
  await waitFor(() => expect(ack.mock.calls.some(([, value]) => value === last)).toBe(true), { timeout: 3500 });
});

// 所有 Remotion 接口与 SSE 都携带本地存储的设备 ID；真实服务端按它区分作品归属。
test("Remotion 请求与事件流携带本机设备 ID", async () => {
  const fake = remotionServer();
  await seed("设备归属");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.version?.id).toBe("version-1"));
  await waitFor(() => expect(fake.streams.size).toBe(1));
  const device = await local.deviceId();
  const calls = fetchMock.mock.calls.filter((call) => String(call[0]).includes("/api/templates/"));
  expect(calls.some((call) => String(call[0]).includes("/stream?"))).toBe(true);
  expect(calls.some((call) => String(call[0]).endsWith("/works"))).toBe(true);
  for (const call of calls)
    expect((call[1]?.headers as Record<string, string>)["X-IMV-Device"]).toBe(device);
});

// 保存到资产只允许本机会话的版本，发布请求携带同一设备 ID。
test("保存到资产携带本机设备 ID", async () => {
  fetchMock.mockImplementation((async () =>
    new Response(
      Uint8Array.from(
        toBinary(
          PublishSpriteResponseSchema,
          create(PublishSpriteResponseSchema, {
            sprite: { spriteId: "sprite-1", name: "资产", canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 90 } },
          }),
        ),
      ),
      { headers: { "Content-Type": "application/x-protobuf" } },
    )) as unknown as typeof fetch);
  await publishSprite("version-1", { kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "" });
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  expect(new URL(String(url)).pathname).toBe("/api/sprites/publish");
  expect((init?.headers as Record<string, string>)["X-IMV-Device"]).toBe(await local.deviceId());
});
