/** SSE 传输只读取公开事件；支持分块 UTF-8、心跳与中断，由会话协调重连和游标。 */
import { apiUrl } from "./api";
import type { WorkEvent } from "./model";

/** 服务端拒绝旧游标时必须重取快照，不能不断重试失效的事件编号。 */
export class StreamReset extends Error {}

/** 消费标准 SSE 字段；每次调用只建立一条连接，退出时清理 reader、超时与监听。 */
export async function* events(
  work: string,
  after: number,
  signal: AbortSignal,
  onOpen?: () => void,
): AsyncGenerator<WorkEvent> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  let timeout: number;
  /** 服务端每 15 秒发心跳；45 秒无字节视为失联，避免永远停在制作中。 */
  function touch() {
    window.clearTimeout(timeout);
    timeout = window.setTimeout(abort, 45_000);
  }
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    touch();
    const response = await fetch(
      apiUrl(`/works/${encodeURIComponent(work)}/stream?after=${after}`),
      {
        signal: controller.signal,
        headers: {
          Accept: "text/event-stream",
          ...(after ? { "Last-Event-ID": String(after) } : {}),
        },
      },
    );
    if (response.status === 409)
      throw new StreamReset("会话游标已变化，正在恢复历史。");
    if (
      !response.ok ||
      !response.body ||
      !response.headers.get("content-type")?.startsWith("text/event-stream")
    )
      throw new Error("事件连接未建立。");
    onOpen?.();
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "",
      data: string[] = [],
      id = "",
      kind = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) throw new Error("事件连接已断开。");
      touch();
      buffer += decoder.decode(chunk.value, { stream: true });
      // Preserve partial lines (including split CRLF) until the next network chunk.
      let match: RegExpExecArray | null;
      while ((match = /\r\n|\n|\r(?!$)/.exec(buffer))) {
        const line = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        if (!line) {
          if (data.length) {
            const event = JSON.parse(data.join("\n")) as WorkEvent;
            if (
              !Number.isSafeInteger(event.id) ||
              event.id <= 0 ||
              String(event.id) !== id ||
              event.type !== kind ||
              event.work_id !== work
            )
              throw new Error("收到不属于当前会话的事件。");
            if (
              ["message.created", "job.updated", "version.ready"].includes(
                event.type,
              )
            )
              yield event;
          }
          data = [];
          id = "";
          kind = "";
        } else if (!line.startsWith(":")) {
          const split = line.indexOf(":");
          const field = split < 0 ? line : line.slice(0, split);
          const value =
            split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
          if (field === "data") data.push(value);
          else if (field === "id") id = value;
          else if (field === "event") kind = value;
        }
      }
    }
  } finally {
    controller.abort();
    window.clearTimeout(timeout!);
    signal.removeEventListener("abort", abort);
    await reader?.cancel().catch(() => {});
    reader?.releaseLock();
  }
}
