/** SSE 字节协议回归：分块、续传、无效游标、坏帧与资源清理；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { events, StreamReset } from "@/features/remotion_templates/events";
import type { WorkEvent } from "@/features/remotion_templates/model";
import { fetchMock } from "./setup";

/** 生成公开消息帧，避免使用模型或真实 HTTP。 */
function event(work = "work-1"): WorkEvent {
  return {
    id: 7,
    work_id: work,
    type: "message.created",
    created_at: "2026-09-14T08:00:00Z",
    data: { id: "m-7", sequence: 7, role: "assistant", text: "中文🎬字效" },
  };
}
/** 任意分块的内存响应保留真实 ReadableStream reader 清理行为。 */
function response(text: string, size = 3) {
  let cancelled = false;
  const bytes = new TextEncoder().encode(text);
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += size)
        controller.enqueue(bytes.slice(i, i + size));
    },
    cancel() {
      cancelled = true;
    },
  });
  fetchMock.mockResolvedValue(
    new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
  );
  return () => cancelled;
}

// UTF-8、CRLF 和 data 多行可以被任意网络分块，心跳和 retry 不应成为聊天消息。
test("SSE 跨字节中文、CRLF、多行数据与续传请求", async () => {
  const item = event();
  const cancelled = response(
    `: heartbeat\r\nretry: 2000\r\n\r\nid: 7\r\nevent: message.created\r\ndata: {\r\ndata: ${JSON.stringify(item).slice(1)}\r\n\r\n`,
    1,
  );
  const stream = events("work-1", 6, new AbortController().signal);
  expect((await stream.next()).value).toEqual(item);
  await stream.return(undefined);
  expect(cancelled()).toBe(true);
  expect(String(fetchMock.mock.calls[0][0])).toContain("after=6");
  expect(fetchMock.mock.calls[0][1]?.headers).toEqual({
    Accept: "text/event-stream",
    "Last-Event-ID": "6",
  });
});

// 游标已不属于当前数据库时明确要求恢复快照，普通错误不误判为成功连接。
test("SSE 游标失效和非流响应显式失败", async () => {
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 409 }));
  await expect(
    events("work-1", 6, new AbortController().signal).next(),
  ).rejects.toBeInstanceOf(StreamReset);
  fetchMock.mockResolvedValueOnce(Response.json({ unexpected: true }));
  await expect(
    events("work-1", 0, new AbortController().signal).next(),
  ).rejects.toThrow("事件连接未建立");
});

// 外来会话或帧编号不一致不能推进当前会话游标，reader 仍必须被取消。
test.each(["foreign", "mismatch", "json"])(
  "SSE 拒绝坏帧并清理：%s",
  async (kind) => {
    const item = event(kind === "foreign" ? "work-2" : "work-1");
    const cancelled = response(
      `id: ${kind === "mismatch" ? 9 : 7}\nevent: message.created\ndata: ${kind === "json" ? "invalid-json" : JSON.stringify(item)}\n\n`,
    );
    await expect(
      events("work-1", 0, new AbortController().signal).next(),
    ).rejects.toThrow();
    expect(cancelled()).toBe(true);
  },
);

// 无心跳的连接最终中止；中断会清理定时器，不触发任何写请求。
test("SSE 心跳超时中止静默连接", async () => {
  const timer = spyOn(window, "setTimeout");
  let aborted = false;
  fetchMock.mockImplementation(
    Object.assign(
      (_input: RequestInfo | URL, options?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          options?.signal?.addEventListener(
            "abort",
            () => {
              aborted = true;
              reject(new DOMException("Aborted", "AbortError"));
            },
            { once: true },
          );
        }),
      { preconnect: () => {} },
    ),
  );
  const pending = events("work-1", 0, new AbortController().signal).next();
  const callback = timer.mock.calls.find((call) => call[1] === 45_000)?.[0];
  expect(typeof callback).toBe("function");
  if (typeof callback === "function") callback();
  await expect(pending).rejects.toThrow();
  expect(aborted).toBe(true);
  expect(fetchMock.mock.calls).toHaveLength(1);
});
