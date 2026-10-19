/**
 * 本地协议替身：按作品缓存未送达的公开事件，经真实 fetch/SSE 与分页事件接口重放，并实现送达确认；不连接模型或播放器。
 *
 * 会话历史保存在客户端本地存储（测试中为内存实现），替身不再提供会话快照或历史分页。`seed` 模拟本机此前
 * 已创建并写入过的会话：调用创建接口后，把该作品已有事件写入本地存储。
 */
import { fetchMock } from "./setup";
import { remotionJob, remotionVersion } from "./remotion-fixtures";
import * as api from "@/features/remotion_templates/api";
import { apply, record } from "@/features/remotion_templates/localSessions";
import type {
  ChatMessage,
  Job,
  SessionJob,
  WorkEvent,
} from "@/features/remotion_templates/model";

/** 待送达事件分页接口每页的条数；取小值让测试覆盖多页读取。 */
const EVENT_PAGE = 2;
/** 当前测试的替身；`seed` 从它读取已发出的事件。 */
let active: ReturnType<typeof remotionServer> | undefined;

/** 显式覆盖 HTTP 分支可模拟失败；未覆盖读写使用内存事件和可控制的 SSE。 */
export function remotionServer(
  overrides: (
    path: string,
    options?: RequestInit,
    url?: URL,
  ) => Response | Promise<Response> | undefined = () => undefined,
) {
  const works = new Set<string>();
  const jobs = new Map<string, SessionJob>();
  const records: WorkEvent[] = [];
  // 已发出的全部事件，不随确认删除；用于重放重复事件等断言。
  const emitted: WorkEvent[] = [];
  const acks = new Map<string, number[]>();
  const streams = new Set<{
    work: string;
    controller: ReadableStreamDefaultController<Uint8Array>;
  }>();
  let sequence = 0,
    created = 0,
    nextId = 0;
  const versions = new Map<string, string>();
  /** 每条测试事件具有单调 ID，保存到确认送达前，并推送给该作品的已有连接。 */
  function emit(work: string, payload: Pick<WorkEvent, "type" | "data">) {
    const event = {
      id: ++nextId,
      work_id: work,
      created_at: "2026-09-14T08:01:08Z",
      ...payload,
    } as WorkEvent;
    records.push(event);
    emitted.push(event);
    if (event.type === "job.updated")
      jobs.set(work, { ...jobs.get(work), ...event.data });
    for (const stream of streams)
      if (stream.work === work) stream.controller.enqueue(encode(event));
  }
  /** 一条公开消息只以事件形式送达客户端。 */
  function message(
    work: string,
    job: Job,
    role: ChatMessage["role"],
    text: string,
    images: string[] = [],
  ) {
    emit(work, {
      type: "message.created",
      data: {
        id: `message-${++sequence}`,
        sequence,
        job_id: job.id,
        role,
        text,
        image_asset_ids: images,
        created_at: "2026-09-14T08:00:00Z",
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
      progress: (jobs.get(work)?.progress ?? []).map((step) =>
        step.status === "active" && !["running", "queued"].includes(job.status)
          ? {
              ...step,
              status: ["succeeded", "answered", "needs_input"].includes(
                job.status,
              )
                ? "done"
                : "stopped",
              ended_at: "2026-09-14T08:01:08Z",
            }
          : step,
      ),
    };
    emit(work, { type: "job.updated", data: state });
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
    } else if (job.status === "answered")
      message(work, job, "assistant", job.message!);
    else if (job.status === "needs_input")
      message(work, job, "assistant", job.questions.join("\n"));
    else if (["failed", "cancelled", "interrupted"].includes(job.status))
      message(
        work,
        job,
        "assistant",
        job.message || "已停止本次制作，已有成功模板仍可使用。",
      );
  }
  /** POST 回执生成公开事件；创建回执补齐客户端登记本地会话所需的作品字段。 */
  async function capture(
    path: string,
    options: RequestInit | undefined,
    response: Response,
  ) {
    if (options?.method !== "POST" || !response.ok || !response.headers.get("Content-Type")?.includes("application/json") || path === "/assets" || path.endsWith("/ack"))
      return response;
    const body = await response.clone().json();
    const job: Job = body.job ?? body;
    if (!job.project_id) return response;
    const input =
      typeof options.body === "string" ? JSON.parse(options.body) : {};
    works.add(job.project_id);
    if (!path.endsWith("/cancel"))
      message(
        job.project_id,
        job,
        "user",
        input.description ||
          input.instruction ||
          (input.parameters ? "调整模板参数。" : "请参考这张图片制作字效。"),
        (input.images ?? []).map((image: { asset_id: string }) => image.asset_id),
      );
    advance(job);
    if (path === "/works" && body.work)
      return Response.json({
        ...body,
        work: {
          current_version_id: null,
          created_at: "2026-09-14T08:00:00Z",
          request: { description: input.description ?? null },
          ...body.work,
        },
      });
    return response;
  }
  /** 删除替身清理关联记录并给已有连接发送无游标终止信号。 */
  function remove(work: string) {
    works.delete(work);
    for (let index = records.length - 1; index >= 0; index--)
      if (records[index]!.work_id === work) records.splice(index, 1);
    for (const [id, owner] of versions) if (owner === work) versions.delete(id);
    for (const stream of streams)
      if (stream.work === work) {
        stream.controller.enqueue(
          new TextEncoder().encode(
            `event: work.deleted\ndata: ${JSON.stringify({ work_id: work })}\n\n`,
          ),
        );
      }
  }
  /** 确认送达：记录游标并删除该作品不大于游标的事件。 */
  function acknowledge(work: string, cursor: number) {
    acks.set(work, [...(acks.get(work) ?? []), cursor]);
    for (let index = records.length - 1; index >= 0; index--)
      if (records[index]!.work_id === work && records[index]!.id <= cursor)
        records.splice(index, 1);
  }
  fetchMock.mockImplementation(
    Object.assign(
      async (input: RequestInfo | URL, options?: RequestInit) => {
        const url = new URL(String(input));
        const path = url.pathname.replace("/api/templates", "");
        const custom = overrides(path, options, url);
        if (custom) return capture(path, options, await custom);
        if (/^\/works\/[^/]+$/.test(path) && options?.method === "DELETE") {
          remove(path.split("/")[2]!);
          return new Response(null, { status: 204 });
        }
        if (/^\/works\/[^/]+\/ack$/.test(path) && options?.method === "POST") {
          acknowledge(path.split("/")[2]!, JSON.parse(String(options.body)).cursor);
          return new Response(null, { status: 204 });
        }
        if (path === "/capabilities")
          return Response.json({ models_configured: true });
        if (path === "/works" && options?.method === "POST") {
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
        if (/^\/jobs\/[^/]+$/.test(path) && (options?.method ?? "GET") === "GET") {
          const job = [...jobs.values()].find((item) => item.id === path.split("/")[2]);
          return job ? Response.json(job) : new Response(null, { status: 404 });
        }
        if (/^\/works\/[^/]+\/versions$/.test(path)) {
          const work = path.split("/")[2]!;
          return Response.json(
            [...versions]
              .filter(([, owner]) => owner === work)
              .map(([id]) => ({ ...remotionVersion(id), project_id: work })),
          );
        }
        if (/^\/works\/[^/]+\/events$/.test(path)) {
          const work = path.split("/")[2]!;
          if (!works.has(work)) return new Response(null, { status: 404 });
          const after = Number(url.searchParams.get("after"));
          // 每页最多 EVENT_PAGE 条，后台同步须继续读取直到取空。
          return Response.json(
            records.filter((event) => event.work_id === work && event.id > after).slice(0, EVENT_PAGE),
          );
        }
        if (path.endsWith("/stream")) {
          const work = path.split("/")[2]!;
          if (!works.has(work)) return new Response(null, { status: 404 });
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
        if (/^\/versions\/[^/]+\/diagnostics$/.test(path))
          return Response.json({ passed: true, diagnostics: [] });
        if (path.endsWith("/artifacts/Export.tsx"))
          return new Response(
            'export default function Template() { return "今日灵感"; }',
          );
        throw new Error(`未配置请求：${path}`);
      },
      { preconnect: () => {} },
    ),
  );
  const server = { works, advance, emit, streams, records, emitted, acks, remove };
  active = server;
  return server;
}

/** 模拟本机此前创建并已写入本地的会话：调用创建接口，再把该作品已有事件写入本地存储。 */
export async function seed(description: string, assets: string[] = []) {
  const created = await api.create(description, assets);
  await record(created.work, created.job);
  await apply(created.work.id, active!.records.filter((event) => event.work_id === created.work.id));
  return created;
}

/** SSE 分帧遵循真实接口的 id/event/data 格式。 */
function encode(event: WorkEvent) {
  return new TextEncoder().encode(
    `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
  );
}
