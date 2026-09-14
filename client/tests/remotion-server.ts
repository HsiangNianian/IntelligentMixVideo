/** 本地协议替身保存公开消息和事件，供 UI 测试驱动真实 fetch/SSE 读取而不连接模型或播放器。 */
import { fetchMock } from "./setup";
import { remotionJob, remotionVersion } from "./remotion-fixtures";
import type {
  ChatMessage,
  Job,
  SessionJob,
  SessionSnapshot,
  WorkEvent,
  WorkSummary,
} from "@/features/remotion_templates/model";

/** 显式覆盖 HTTP 分支可模拟失败；未覆盖读写使用内存会话和可控制的 SSE。 */
export function remotionServer(
  overrides: (
    path: string,
    options?: RequestInit,
    url?: URL,
  ) => Response | Promise<Response> | undefined = () => undefined,
) {
  const snapshots = new Map<string, SessionSnapshot>();
  const summaries = new Map<string, WorkSummary>();
  const records: WorkEvent[] = [];
  const streams = new Set<{
    work: string;
    controller: ReadableStreamDefaultController<Uint8Array>;
  }>();
  let sequence = 0,
    created = 0;
  const versions = new Map<string, string>();
  /** 每条测试事件具有单调 ID，并立即写入当前会话快照，模拟同事务持久化。 */
  function emit(work: string, payload: Pick<WorkEvent, "type" | "data">) {
    const event = {
      id: records.length + 1,
      work_id: work,
      created_at: "2026-09-14T08:01:08Z",
      ...payload,
    } as WorkEvent;
    records.push(event);
    const snap = snapshots.get(work)!;
    snap.cursor = event.id;
    if (event.type === "message.created") snap.messages.push(event.data);
    else if (event.type === "job.updated") snap.job = event.data;
    else snap.work.current_version_id = event.data.version_id;
    for (const stream of streams)
      if (stream.work === work) stream.controller.enqueue(encode(event));
  }
  /** 一条公开消息同时支持聊天快照恢复和 SSE 重放。 */
  function message(
    work: string,
    job: Job,
    role: ChatMessage["role"],
    text: string,
    image?: string,
  ) {
    emit(work, {
      type: "message.created",
      data: {
        id: `message-${++sequence}`,
        sequence,
        job_id: job.id,
        role,
        text,
        image_asset_id: image,
        created_at: "2026-09-14T08:00:00Z",
        reconstructed: false,
      },
    });
  }
  /** 手动完成运行任务；版本只在成功事件中出现，失败不会清除上一成功指针。 */
  function advance(job: Job) {
    const work = job.project_id;
    const state: SessionJob = {
      ...job,
      created_at: "2026-09-14T08:00:00Z",
      updated_at: "2026-09-14T08:01:08Z",
      parameters: null,
    };
    emit(work, { type: "job.updated", data: state });
    const summary = summaries.get(work);
    if (summary) summary.job = state;
    if (job.status === "succeeded" && job.result_version_id) {
      versions.set(job.result_version_id, work);
      emit(work, {
        type: "version.ready",
        data: { version_id: job.result_version_id, job_id: job.id },
      });
      message(
        work,
        job,
        "assistant",
        "模板已就绪。可以调整参数，或继续描述你想修改的效果。",
      );
    } else if (job.status === "needs_input")
      message(work, job, "assistant", job.questions.join("\n"));
    else if (["failed", "cancelled", "interrupted"].includes(job.status))
      message(
        work,
        job,
        "assistant",
        job.message || "已停止本次制作，已有成功模板仍可使用。",
      );
  }
  /** POST 回执生成公开历史；不通过轮询模拟异步完成。 */
  async function capture(
    path: string,
    options: RequestInit | undefined,
    response: Response,
  ) {
    if (options?.method !== "POST" || !response.ok || path === "/assets")
      return response;
    const body = await response.clone().json();
    const job: Job = body.job ?? body;
    if (!job.project_id) return response;
    const input =
      typeof options.body === "string" ? JSON.parse(options.body) : {};
    if (!snapshots.has(job.project_id)) {
      snapshots.set(job.project_id, {
        work: { id: job.project_id, current_version_id: null },
        job: {
          ...job,
          created_at: "2026-09-14T08:00:00Z",
          updated_at: "2026-09-14T08:01:08Z",
          parameters: null,
        },
        messages: [],
        next_before: null,
        cursor: 0,
      });
      summaries.set(job.project_id, {
        id: job.project_id,
        title: input.description || "图片字效",
        updated_at: "2026-09-14T08:01:08Z",
        current_version_id: null,
        job: snapshots.get(job.project_id)!.job,
      });
    }
    if (!path.endsWith("/cancel"))
      message(
        job.project_id,
        job,
        "user",
        input.description ||
          input.instruction ||
          (input.parameters
            ? `调整参数：${JSON.stringify(input.parameters)}`
            : "请参考这张图片制作字效。"),
        input.image?.asset_id,
      );
    advance(job);
    return response;
  }
  fetchMock.mockImplementation(
    Object.assign(
      async (input: RequestInfo | URL, options?: RequestInit) => {
        const url = new URL(String(input));
        const path = url.pathname.replace("/api/templates", "");
        if (path === "/works" && options?.method !== "POST")
          return Response.json({
            items: [...summaries.values()],
            next_cursor: null,
          });
        const custom = overrides(path, options, url);
        if (custom) return capture(path, options, await custom);
        if (path === "/capabilities")
          return Response.json({ models_configured: true });
        if (path === "/works") {
          const id = `work-${++created}`;
          return capture(
            path,
            options,
            Response.json({
              work: { id },
              job: {
                ...remotionJob("succeeded", `version-${created}`),
                project_id: id,
              },
            }),
          );
        }
        if (path.endsWith("/session"))
          return Response.json(snapshots.get(path.split("/")[2]!) ?? {}, {
            status: snapshots.has(path.split("/")[2]!) ? 200 : 404,
          });
        if (path.endsWith("/stream")) {
          const work = path.split("/")[2]!;
          const after = Number(url.searchParams.get("after"));
          let stream: {
            work: string;
            controller: ReadableStreamDefaultController<Uint8Array>;
          };
          const body = new ReadableStream<Uint8Array>({
            start(controller) {
              stream = { work, controller };
              streams.add(stream);
              controller.enqueue(new TextEncoder().encode(": connected\n\n"));
              for (const event of records)
                if (event.work_id === work && event.id > after)
                  controller.enqueue(encode(event));
              options?.signal?.addEventListener(
                "abort",
                () => {
                  streams.delete(stream);
                  controller.error(new DOMException("Aborted", "AbortError"));
                },
                { once: true },
              );
            },
            cancel() {
              streams.delete(stream);
            },
          });
          return new Response(body, {
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        if (
          /^\/versions\/[^/]+$/.test(path) &&
          versions.has(path.split("/")[2]!)
        ) {
          const id = path.split("/")[2]!;
          return Response.json({
            ...remotionVersion(id),
            project_id: versions.get(id),
          });
        }
        if (path.endsWith("/artifacts/Export.tsx"))
          return new Response(
            'export default function Template() { return "今日灵感"; }',
          );
        throw new Error(`未配置请求：${path}`);
      },
      { preconnect: () => {} },
    ),
  );
  return { snapshots, summaries, advance, emit, streams, records };
}

/** SSE 分帧遵循真实接口的 id/event/data 格式。 */
function encode(event: WorkEvent) {
  return new TextEncoder().encode(
    `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
  );
}
