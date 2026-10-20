/**
 * 字效本地会话库回归：事件合并规则（任务状态、轮次、游标、作品版本指针）、消息分页、写入本地后确认送达，
 * 以及 Remotion 请求和资产发布携带本机设备 ID；存储使用每例独立的内存实现。桌面存储经 IPC 发送的参数与
 * `remotion-sessions-contract.json` 逐字比对，Rust 单元测试解析并执行同一份夹具。浏览器的 IndexedDB 实现用
 * fake-indexeddb 验证页面重新加载后的读回、按服务删除与 strict 持久性提交。执行 bun run test。
 */
import { expect, spyOn, test } from "bun:test";
import { IDBDatabase, IDBFactory, IDBKeyRange } from "fake-indexeddb";
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
import { fetchMock, mockDesktop } from "./setup";
import contract from "./remotion-sessions-contract.json";
import { apiBase, setApiBase } from "@/lib/api-base";

const ROUND: LoopRound = { layer: "outer", turn: 1, calls: [{ tool: "tools.plan_execute", status: "pass" }] };

/** 一条服务端事件，编号与类型由调用方指定。 */
function event(id: number, payload: Pick<WorkEvent, "type" | "data">): WorkEvent {
  return { id, work_id: "work-1", created_at: `2026-09-14T08:00:${String(id).padStart(2, "0")}Z`, ...payload } as WorkEvent;
}
/** 运行中的公开任务状态；服务端的状态事件不携带轮次。 */
function running(status: SessionJob["status"] = "running"): SessionJob {
  return { ...remotionJob(status), created_at: "2026-09-14T08:00:00Z", updated_at: "2026-09-14T08:00:00Z", parameters: null };
}
/**
 * 模拟 Rust `remotion_sessions` 命令：按操作名与参数把调用交给内存实现，记录 IPC 序列化后的参数；
 * `fail` 返回 true 的操作像 Rust 一样以字符串错误拒绝。
 */
function desktopStorage(fail: (operation: string) => boolean = () => false) {
  const memory = local.memoryStorage();
  const calls: { operation: string; payload: Record<string, unknown> }[] = [];
  /** 与 Rust 命令相同的操作分派；参数先经 JSON 往返，和真实 IPC 一样丢弃 undefined 字段。 */
  async function serve(args: Record<string, unknown>) {
    const call = JSON.parse(JSON.stringify(args)) as { operation: string; payload: Record<string, never> };
    calls.push(call);
    const { operation, payload } = call;
    if (fail(operation)) throw "读写本地会话库失败";
    if (operation === "device") return memory.device();
    if (operation === "works") return memory.works(payload.server);
    if (operation === "work") return memory.work(payload.server, payload.id);
    if (operation === "messages") return memory.messages(payload.server, payload.work, payload.before, payload.limit);
    if (operation === "jobs") return memory.jobs(payload.server, payload.work, payload.ids);
    if (operation === "save") {
      const { server, ...change } = payload;
      return memory.save(server, change as unknown as local.SessionChange);
    }
    if (operation === "remove") return memory.remove(payload.server, payload.work);
    throw new Error(`未知的本地会话操作：${operation}`);
  }
  // 模型请求读取桌面设置；这里没有保存过的设置。
  mockDesktop(async (command, args) => {
    if (command === "remotion_sessions") return serve(args);
    if (command === "local_settings") return {};
    throw new Error(`未模拟命令：${command}`);
  });
  local.setStorage(undefined);
  return calls;
}
/**
 * 在一个全新的 fake-indexeddb 里执行 `body`，模拟浏览器版的本地会话库；之后恢复服务地址并移除全局 IndexedDB，
 * 下一例由测试初始化换回内存实现。
 */
async function browserDatabase(body: (factory: IDBFactory) => Promise<void>) {
  const factory = new IDBFactory();
  Reflect.set(globalThis, "indexedDB", factory);
  Reflect.set(globalThis, "IDBKeyRange", IDBKeyRange);
  const original = apiBase();
  try {
    local.setStorage(undefined);
    await body(factory);
  } finally {
    setApiBase(original);
    Reflect.deleteProperty(globalThis, "indexedDB");
    Reflect.deleteProperty(globalThis, "IDBKeyRange");
  }
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
  expect(stored?.cursor).toBe(4);
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
  expect((await local.history())[0]).toMatchObject({ id: "work-1", current_version_id: "version-9", title: "本地标题" });
});

// 同一会话的读写串行：恢复读取排在进行中的事件写入之后，读到的游标包含这批事件；后台同步与 SSE 不会交错写入。
test("同一会话的快照等待进行中的写入", async () => {
  await recordWork();
  const save = local.storage().save;
  let release!: () => void;
  spyOn(local.storage(), "save").mockImplementationOnce(async (...args) => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    return save(...args);
  });
  const writing = local.apply("work-1", [event(1, { type: "job.updated", data: running() })]);
  const reading = local.snapshot("work-1");
  await waitFor(() => expect(release).toBeDefined());
  release();
  await writing;
  expect((await reading).cursor).toBe(1);
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
  await expect(local.snapshot("missing")).rejects.toBeInstanceOf(local.MissingLocalWork);
});

// 回归：浏览器曾只把会话存在页面内存，刷新或重启后聊天历史和设备 ID 一起丢失（设备 ID 变了，服务端也认不出原作品）。
// 现在保存在 IndexedDB：模拟页面重新加载（新的存储实例与连接）后会话、消息分页、任务状态与设备 ID 原样读回；
// 删除只移除当前服务下的这条会话。
test("浏览器重新加载后从 IndexedDB 读回会话与设备 ID", () =>
  browserDatabase(async () => {
    const original = apiBase();
    const device = await local.deviceId();
    await recordWork();
    await local.apply("work-1", [
      ...[1, 2, 3].map((sequence) =>
        event(sequence, {
          type: "message.created",
          data: { id: `m-${sequence}`, sequence, job_id: "job-1", role: "user", text: `第 ${sequence} 条` },
        }),
      ),
      event(4, { type: "job.updated", data: running("succeeded") }),
    ]);
    setApiBase("http://other.test:9000");
    await recordWork();
    setApiBase(original);

    local.setStorage(undefined);
    expect(await local.deviceId()).toBe(device);
    expect(await local.history()).toMatchObject([{ id: "work-1", title: "本地标题", job: { status: "succeeded" } }]);
    const first = await local.snapshot("work-1", undefined, 2);
    expect(first.messages.map((message) => message.text)).toEqual(["第 2 条", "第 3 条"]);
    expect(first.cursor).toBe(4);
    expect((await local.snapshot("work-1", first.next_before!, 2)).messages.map((message) => message.sequence)).toEqual([1]);

    await local.forget("work-1");
    expect(await local.history()).toEqual([]);
    expect(await local.storage().messages(local.server(), "work-1", null, 10)).toEqual([]);
    setApiBase("http://other.test:9000");
    expect((await local.history()).map((work) => work.id)).toEqual(["work-1"]);
  }));

// 回归：浏览器默认以 relaxed 提交，写入只到系统缓冲；服务端在确认送达后删除事件，系统崩溃或断电会丢失已确认的消息，
// 首次生成的设备 ID 丢失还会让本机认不出原作品。会话库的每个事务都以 strict 提交。
test("浏览器会话库以 strict 持久性提交", () =>
  browserDatabase(async () => {
    const opened = spyOn(IDBDatabase.prototype, "transaction");
    try {
      await local.deviceId();
      await recordWork();
      await local.snapshot("work-1");
      await local.forget("work-1");
      // 设备 ID、登记会话与删除各一次写事务；建表的 versionchange 事务由打开请求创建，不经过这里的选项。
      const writes = opened.mock.results
        .map((result) => result.value as IDBTransaction)
        .filter((transaction) => transaction.mode === "readwrite");
      expect(writes.map((transaction) => transaction.durability)).toEqual(["strict", "strict", "strict"]);
    } finally {
      opened.mockRestore();
    }
  }));

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

// 标题按码位截取前 40 个字符：第 40 个字符是 emoji 时完整保留，标题仍是合法的 UTF-16，可经 IPC 序列化。
test("会话标题不会截断 emoji", async () => {
  const description = `${"字".repeat(39)}😀尾巴`;
  await local.record(
    { id: "work-1", current_version_id: null, created_at: "2026-09-14T08:00:00Z", request: { description } },
    remotionJob("queued"),
  );
  const [{ title }] = await local.history();
  expect(title).toBe(`${"字".repeat(39)}😀`);
  expect(new TextDecoder().decode(new TextEncoder().encode(title))).toBe(title);
});

// 新作品创建成功但本地登记失败时撤销服务端作品，提示本地错误并恢复草稿，不留下本机看不到的作品。
test("本地登记失败时撤销刚创建的作品", async () => {
  const fake = remotionServer();
  spyOn(local.storage(), "save").mockRejectedValueOnce(new local.LocalStoreError("读写本地会话库失败"));
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("登记失败"));
  await waitFor(() => expect(result.current.error).toContain("本地会话保存失败：读写本地会话库失败"));
  expect(result.current.error).toContain("已撤销本次创建");
  expect(result.current.workId).toBeNull();
  expect(result.current.busy).toBeNull();
  expect(result.current.messages).toEqual([]);
  expect(result.current.draft?.text).toBe("登记失败");
  expect(fake.works.has("work-1")).toBe(false);
  const deletes = fetchMock.mock.calls.filter(([, options]) => options?.method === "DELETE");
  expect(deletes.map(([url]) => new URL(String(url)).pathname)).toEqual(["/api/templates/works/work-1"]);
  expect(await local.history()).toEqual([]);
});

// 本地会话库写入失败（Rust 以字符串报错）时停止接收更新并显示可刷新的错误，不当作断线无限重连；修复后显式刷新即续传。
test("本地会话库失败时显示错误且不反复重连", async () => {
  let broken = false;
  desktopStorage((operation) => broken && operation === "save");
  const fake = remotionServer();
  await seed("本地失败");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  broken = true;
  await act(async () => fake.advance({ ...remotionJob("answered"), id: "job-2", message: "已回答" }));
  await waitFor(() => expect(result.current.error).toContain("读写本地会话库失败"));
  expect(result.current.connection).toBeNull();
  expect(result.current.retryMode).toBe("read");
  await act(() => new Promise((resolve) => setTimeout(resolve, 2300)));
  /** 已发起的 SSE 连接数。 */
  const streams = () => fetchMock.mock.calls.filter((call) => String(call[0]).includes("/stream?")).length;
  expect(streams()).toBe(1);
  broken = false;
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.job?.id).toBe("job-2"));
  expect(result.current.connection).toBe("live");
  expect(result.current.error).toBe("");
  expect(streams()).toBe(2);
});

// 终态事件已过期、本机停在运行中：打开会话时读取任务状态校正，输入不再被锁定。
test("打开停在运行中的会话时校正为服务端终态", async () => {
  const fake = remotionServer((path, options) =>
    path === "/works" && options?.method === "POST"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : undefined,
  );
  await seed("过期");
  await act(async () => fake.advance(remotionJob("succeeded", "version-1")));
  fake.records.length = 0;
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.job?.status).toBe("succeeded"));
  expect(result.current.busy).toBeNull();
  expect((await local.snapshot("work-1")).work.current_version_id).toBe("version-1");
});

// 「停止」遇到服务端早已结束的任务：取消接口回执的终态写入本地，会话解除锁定，不必删除重建。
test("停止停在运行中的过期会话时采用服务端回执的终态", async () => {
  const fake = remotionServer((path, options) =>
    path === "/works" && options?.method === "POST"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : path.endsWith("/cancel")
        ? Response.json(remotionJob("failed"))
        : undefined,
  );
  await seed("过期停止");
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  // 服务端在本机不知情时已结束任务，也没有事件可送。
  fake.records.length = 0;
  fake.works.add("work-1");
  expect(result.current.busy).toBe("chat");
  const reconcile = spyOn(local, "reconcile");
  await act(async () => result.current.stop());
  await waitFor(() => expect(result.current.busy).toBeNull());
  expect(result.current.job?.status).toBe("failed");
  // 回执的终态由停止操作直接写入本地，不依赖事件再送达。
  expect(reconcile.mock.calls.map(([job]) => [job.id, job.status])).toEqual([["job-1", "failed"]]);
});

// 会话按 API 服务地址归属：切换服务后历史只列该服务的会话，删除也只移除该服务下的本地记录；同一服务的不同写法视为同一个。
test("本地会话按服务地址隔离", async () => {
  const original = apiBase();
  try {
    await recordWork();
    setApiBase("http://other.test:9000");
    expect(await local.history()).toEqual([]);
    await expect(local.snapshot("work-1")).rejects.toBeInstanceOf(local.MissingLocalWork);
    await local.forget("work-1");
    setApiBase(original.toUpperCase().replace("HTTP://", "http://") + "/");
    expect((await local.history()).map((work) => work.id)).toEqual(["work-1"]);
    expect((await local.snapshot("work-1")).work.id).toBe("work-1");
  } finally {
    setApiBase(original);
  }
});

// 桌面存储经 IPC 发送的每种操作与参数结构固定在共享夹具中；Rust 单元测试按同一夹具解析并执行，两端不会悄悄失配。
test("桌面会话存储的 IPC 参数符合 Rust 契约夹具", async () => {
  const calls = desktopStorage();
  await local.deviceId();
  await recordWork();
  await local.apply("work-1", [
    event(1, {
      type: "message.created",
      data: { id: "m-1", sequence: 1, job_id: "job-1", role: "user", text: "契约", image_asset_ids: [], created_at: "2026-09-14T08:00:00Z" },
    }),
    event(2, { type: "job.updated", data: running() }),
    event(3, { type: "job.round", data: { job_id: "job-1", round: ROUND } }),
    event(4, { type: "version.ready", data: { version_id: "version-1", job_id: "job-1" } }),
  ]);
  await local.snapshot("work-1");
  await local.history();
  await local.markDeleting("work-1");
  await local.forget("work-1");
  expect(calls).toEqual(contract);
});
