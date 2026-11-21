/** SSE 传输只读取尚未送达的公开事件；支持分块 UTF-8、心跳与中断，由会话协调重连、本地写入和送达确认。 */
import { ApiError, apiUrl } from "./api";
import { deviceId } from "./localSessions";
import type { WorkEvent } from "./model";
import { beginDiagnostic, briefly, diagnosticTaskState } from "@/lib/diagnostics";

/** 消费标准 SSE 字段；每次调用只建立一条连接，退出时清理 reader、超时与监听。 */
export async function* events(
  work: string,
  after: number,
  signal: AbortSignal,
  onOpen?: () => void,
): AsyncGenerator<WorkEvent> {
  const trace = beginDiagnostic("前端实时连接", `订阅会话 ${work}，从事件 ${after} 续传`);
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
    const device = await deviceId();
    const response = await trace.run(() => fetch(
      apiUrl(`/works/${encodeURIComponent(work)}/stream?after=${after}`),
      {
        signal: controller.signal,
        headers: {
          Accept: "text/event-stream",
          "X-IMV-Device": device,
          ...(after ? { "Last-Event-ID": String(after) } : {}),
        },
      },
    ));
    if (response.status === 404 || response.status === 410)
      throw new ApiError(response.status, "会话已删除或正在清理。");
    if (
      !response.ok ||
      !response.body ||
      !response.headers.get("content-type")?.startsWith("text/event-stream")
    )
      throw new Error("事件连接未建立。");
    onOpen?.();
    trace.step("SSE 已建立；收到事件后先写本机库，再确认送达");
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
            const payload = JSON.parse(data.join("\n"));
            if (kind === "work.deleted" && payload.work_id === work)
              throw new ApiError(410, "会话已删除或正在清理。");
            const event = payload as WorkEvent;
            if (
              !Number.isSafeInteger(event.id) ||
              event.id <= 0 ||
              String(event.id) !== id ||
              event.type !== kind ||
              event.work_id !== work
            )
              throw new Error("收到不属于当前会话的事件。");
            if (
              ["message.created", "job.updated", "job.round", "version.ready"].includes(
                event.type,
              )
            ) {
              trace.step(`收到事件 ${event.id}：${event.type}`);
              if (event.type === "job.updated") {
                const progress = event.data.progress;
                diagnosticTaskState("字效", event.data.id, event.data.status, progress?.[progress.length - 1]?.phase, event.data.message ?? "");
              }
              yield event;
            }
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
  } catch (error) {
    trace.finish(signal.aborted ? "订阅已取消" : `连接中断：${briefly(error)}`, signal.aborted ? "信息" : "警告");
    throw error;
  } finally {
    trace.finish("订阅已关闭");
    controller.abort();
    window.clearTimeout(timeout!);
    signal.removeEventListener("abort", abort);
    await reader?.cancel().catch(() => {});
    reader?.releaseLock();
  }
}
