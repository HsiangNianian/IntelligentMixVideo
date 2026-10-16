/**
 * 字效会话的本地存储：聊天消息、任务公开状态与本机设备 ID 只保存在使用者这一端；版本列表与代码按需从服务端读取。
 *
 * 桌面通过 Tauri `remotion_sessions` 命令读写应用数据目录下的 SQLite；浏览器仅供本地测试，使用页面内存，
 * 刷新即清空。会话按当前 API 服务地址归属，切换服务只看到该服务下的会话。同一会话的读写按调用顺序串行，
 * 打开会话的 SSE 订阅与后台同步（`backgroundSync.ts`）不会交错写入。两种实现共用同一存储接口，事件合并规则只在本文件实现一次：会话订阅把服务端事件交给
 * `apply` 写入本地后，再向服务端确认送达，服务端随即删除已送达的事件。
 */
import { updateInvoke as invoke } from "@/lib/update-guard";
import { isTauri } from "@tauri-apps/api/core";
import { apiBase } from "@/lib/api-base";
import type {
  ChatMessage,
  Job,
  SessionJob,
  SessionSnapshot,
  WorkEvent,
  WorkSummary,
} from "./model";

/**
 * 一条本地会话：作品指针、最新任务、侧栏标题、最后活动时间与已写入的最后事件编号。
 * `deleting` 表示服务端已报告该作品不存在或正在清理（404/410）：本地记录保留到用户在侧栏重试删除。
 */
export interface StoredWork {
  work: { id: string; current_version_id: string | null };
  title: string;
  job: SessionJob;
  updated_at: string;
  cursor: number;
  deleting?: boolean;
}
/** 一次原子写入：会话行与本批新增或更新的消息、任务。 */
export interface SessionChange {
  work: StoredWork;
  messages: ChatMessage[];
  jobs: SessionJob[];
}
/**
 * 本地存储后端。会话及其消息、任务按服务地址 `server` 归属（见 `server()`），不同服务的记录互不可见；
 * 设备 ID 全局唯一。messages 按序号从新到旧返回，`before` 为 null 时从最新开始。
 */
export interface SessionStorage {
  device(): Promise<string>;
  works(server: string): Promise<StoredWork[]>;
  work(server: string, id: string): Promise<StoredWork | null>;
  messages(server: string, work: string, before: number | null, limit: number): Promise<ChatMessage[]>;
  jobs(server: string, work: string, ids: string[]): Promise<SessionJob[]>;
  save(server: string, change: SessionChange): Promise<void>;
  remove(server: string, work: string): Promise<void>;
}

/** 本机没有这条会话（浏览器刷新后内存已清空、记录已删除或属于其他服务）；与服务端 404/410 区分。 */
export class MissingLocalWork extends Error {
  /** 固定提示，调用方通常静默清除选择。 */
  constructor() {
    super("会话不存在于本机。");
  }
}
/** 本地会话库读写失败：与服务端错误区分，会话订阅据此停止并提示，而不是当作断线反复重连。 */
export class LocalStoreError extends Error {}

/** 桌面实现：每个操作是一次 Rust 侧 SQLite 调用，保存在一个事务内完成。 */
function tauriStorage(): SessionStorage {
  /** 统一调用入口；Rust 以字符串返回的中文错误包装为 LocalStoreError，原文展示。 */
  function call<T>(operation: string, payload: Record<string, unknown> = {}) {
    return invoke<T>("remotion_sessions", { operation, payload }).catch((error: unknown) => {
      throw new LocalStoreError(
        typeof error === "string" ? error : error instanceof Error ? error.message : "本地会话库读写失败。",
      );
    });
  }
  return {
    device: () => call("device"),
    works: (server) => call("works", { server }),
    work: (server, id) => call("work", { server, id }),
    messages: (server, work, before, limit) => call("messages", { server, work, before, limit }),
    jobs: (server, work, ids) => call("jobs", { server, work, ids }),
    save: (server, change) => call("save", { server, ...change }),
    remove: (server, work) => call("remove", { server, work }),
  };
}

/** 浏览器测试实现：页面内存保存，刷新后设备 ID 与会话一起丢弃；键为服务地址与记录 ID。 */
export function memoryStorage(): SessionStorage {
  const device = crypto.randomUUID();
  const works = new Map<string, { server: string; work: StoredWork }>();
  const messages = new Map<string, { server: string; work: string; message: ChatMessage }>();
  const jobs = new Map<string, { server: string; job: SessionJob }>();
  /** 同一 ID 在不同服务下是不同记录。 */
  const key = (server: string, id: string) => JSON.stringify([server, id]);
  return {
    device: async () => device,
    works: async (server) =>
      [...works.values()]
        .flatMap((item) => (item.server === server ? [structuredClone(item.work)] : []))
        .sort((left, right) =>
          left.updated_at === right.updated_at
            ? right.work.id.localeCompare(left.work.id)
            : right.updated_at.localeCompare(left.updated_at),
        ),
    work: async (server, id) => structuredClone(works.get(key(server, id))?.work ?? null),
    messages: async (server, work, before, limit) =>
      [...messages.values()]
        .flatMap((item) => (item.server === server && item.work === work ? [structuredClone(item.message)] : []))
        .filter((item) => before === null || (item.sequence ?? 0) < before)
        .sort((left, right) => (right.sequence ?? 0) - (left.sequence ?? 0))
        .slice(0, limit),
    jobs: async (server, work, ids) =>
      ids.flatMap((id) => {
        const job = jobs.get(key(server, id))?.job;
        return job && job.project_id === work ? [structuredClone(job)] : [];
      }),
    save: async (server, change) => {
      const work = change.work.work.id;
      works.set(key(server, work), { server, work: structuredClone(change.work) });
      for (const job of change.jobs) jobs.set(key(server, job.id), { server, job: structuredClone(job) });
      for (const message of change.messages)
        messages.set(key(server, message.id), { server, work, message: structuredClone(message) });
    },
    remove: async (server, work) => {
      works.delete(key(server, work));
      for (const [id, item] of jobs) if (item.server === server && item.job.project_id === work) jobs.delete(id);
      for (const [id, item] of messages) if (item.server === server && item.work === work) messages.delete(id);
    },
  };
}

let backend: SessionStorage | undefined;
let device: Promise<string> | undefined;
/** 桌面用 SQLite，浏览器用内存；测试可替换为独立实例。 */
export function storage(): SessionStorage {
  return (backend ??= isTauri() ? tauriStorage() : memoryStorage());
}
/** 测试替换存储后端，并清除缓存的设备 ID。 */
export function setStorage(next: SessionStorage | undefined) {
  backend = next;
  device = undefined;
}
/** 本机设备 ID 只读取一次；读取失败时下次请求重试。 */
export function deviceId(): Promise<string> {
  device ??= storage()
    .device()
    .catch((error) => {
      device = undefined;
      throw error;
    });
  return device;
}

/**
 * 本机会话归属的服务地址：取当前 API 地址的来源与路径，大小写、默认端口与末尾斜杠不同的写法视为同一服务。
 * 切换服务后只看到该服务的会话，删除也只影响该服务下的本地记录。
 */
export function server(): string {
  const base = apiBase();
  try {
    const url = new URL(base);
    return url.origin + url.pathname.replace(/\/+$/, "");
  } catch {
    return base;
  }
}

/** 每个会话的本地读写队列：SSE、后台同步、恢复读取与删除按调用顺序串行，读到的游标总是上一次写入之后的。 */
const queues = new Map<string, Promise<unknown>>();
/** 在当前服务下该会话的队列末尾执行任务；任务失败只拒绝自己，不阻塞后续任务。 */
function serial<T>(work: string, task: () => Promise<T>): Promise<T> {
  const key = JSON.stringify([server(), work]);
  const run = (queues.get(key) ?? Promise.resolve()).then(task);
  const tail = run.catch(() => {});
  queues.set(key, tail);
  void tail.then(() => {
    if (queues.get(key) === tail) queues.delete(key);
  });
  return run;
}

/** 侧栏摘要只包含标题、活动时间、最新任务与服务端删除状态。 */
function summary(item: StoredWork): WorkSummary {
  return {
    ...(item.deleting ? { deleting: true } : {}),
    id: item.work.id,
    title: item.title,
    updated_at: item.updated_at,
    current_version_id: item.work.current_version_id,
    job: item.job,
  };
}
/** 当前服务下本机全部会话按最近活动排序；本地数量有限，不分页。 */
export async function history(): Promise<WorkSummary[]> {
  return (await storage().works(server())).map(summary);
}
/** 当前服务下仍在排队或运行、且服务端未报告删除的会话；后台同步只拉取它们的待送达事件。 */
export async function pendingWorks(): Promise<StoredWork[]> {
  return (await storage().works(server())).filter(
    (item) => !item.deleting && (item.job.status === "queued" || item.job.status === "running"),
  );
}
/** 从本地恢复与原服务端快照相同的结构；本机没有该会话时视为不存在。排在该会话进行中的写入之后读取。 */
export function snapshot(work: string, before?: number, limit = 50): Promise<SessionSnapshot> {
  return serial(work, () => read(work, before, limit));
}
/** 快照的实际读取。 */
async function read(work: string, before: number | undefined, limit: number): Promise<SessionSnapshot> {
  const scope = server();
  const stored = await storage().work(scope, work);
  if (!stored) throw new MissingLocalWork();
  const rows = await storage().messages(scope, work, before ?? null, limit + 1);
  const messages = rows.slice(0, limit).reverse();
  const ids = [...new Set([...messages.flatMap((item) => (item.job_id ? [item.job_id] : [])), stored.job.id])];
  const jobs = await storage().jobs(scope, work, ids);
  return {
    work: stored.work,
    job: jobs.find((job) => job.id === stored.job.id) ?? stored.job,
    messages,
    next_before: rows.length > limit ? (messages[0]?.sequence ?? null) : null,
    cursor: stored.cursor,
    jobs: jobs.filter((job) => messages.some((item) => item.job_id === job.id)),
  };
}
/** 新建作品后立即登记本地会话，后续消息与状态由事件写入。 */
export function record(
  project: { id: string; current_version_id: string | null; created_at: string; request: { description?: string | null } },
  job: Job,
): Promise<void> {
  const initial: SessionJob = { ...job, created_at: project.created_at, updated_at: project.created_at, parameters: null };
  return serial(project.id, () => storage().save(server(), {
    work: {
      work: { id: project.id, current_version_id: project.current_version_id },
      // 按码位截取：按 UTF-16 截断可能留下半个 emoji，孤立代理项无法经 IPC 序列化。
      title: Array.from(project.request.description || "图片字效")
        .slice(0, 40)
        .join(""),
      job: initial,
      updated_at: project.created_at,
      cursor: 0,
    },
    messages: [],
    jobs: [initial],
  }));
}
/** 提交消息、参数或重试后先登记服务端返回的新任务为最新任务，事件到达前界面也不会回到旧任务状态。 */
export function track(job: Job): Promise<void> {
  return serial(job.project_id, async () => {
    const scope = server();
    const stored = await storage().work(scope, job.project_id);
    if (!stored || stored.job.id === job.id) return;
    const stamp = new Date().toISOString();
    const latest: SessionJob = { ...job, created_at: stamp, updated_at: stamp, parameters: null };
    await storage().save(scope, { work: { ...stored, job: latest }, messages: [], jobs: [latest] });
  });
}
/**
 * 按事件编号顺序把一批服务端事件合并进本地会话并原子保存，返回保存后的会话。
 *
 * 状态事件不含轮次，合并时保留已有轮次；轮次按层级与回合去重；成功版本只更新作品指针。
 * 已写入（编号不大于游标）的事件被忽略，重复投递不会产生重复记录。轮到写入时 `skip` 返回 true 则不写入并返回
 * null：后台同步借此把已在 SSE 中打开的会话留给订阅，两者不会先后写入同一批事件。
 */
export function apply(work: string, events: WorkEvent[], skip?: () => boolean): Promise<StoredWork | null> {
  return serial(work, () => (skip?.() ? Promise.resolve(null) : merge(work, events)));
}
/** 事件合并与保存的实际实现。 */
async function merge(work: string, events: WorkEvent[]): Promise<StoredWork> {
  const scope = server();
  const stored = await storage().work(scope, work);
  if (!stored) throw new MissingLocalWork();
  const fresh = events.filter((event) => event.id > stored.cursor).sort((left, right) => left.id - right.id);
  if (!fresh.length) return stored;
  const wanted = fresh.flatMap((event) =>
    event.type === "job.updated" ? [event.data.id] : event.type === "job.round" ? [event.data.job_id] : [],
  );
  const jobs = new Map((await storage().jobs(scope, work, [...new Set(wanted)])).map((job) => [job.id, job]));
  const messages: ChatMessage[] = [];
  for (const event of fresh) {
    if (event.type === "message.created") messages.push(event.data);
    else if (event.type === "job.updated") {
      const previous = jobs.get(event.data.id) ?? (stored.job.id === event.data.id ? stored.job : undefined);
      const job = { ...previous, ...event.data, rounds: event.data.rounds ?? previous?.rounds ?? [] };
      jobs.set(job.id, job);
      if (job.id === stored.job.id || job.created_at >= stored.job.created_at) stored.job = job;
    } else if (event.type === "job.round") {
      const job = jobs.get(event.data.job_id);
      const round = event.data.round;
      if (job && !(job.rounds ?? []).some((item) => item.layer === round.layer && item.turn === round.turn)) {
        job.rounds = [...(job.rounds ?? []), round];
        if (stored.job.id === job.id) stored.job = job;
      }
    } else {
      stored.work = { ...stored.work, current_version_id: event.data.version_id };
    }
    if (event.created_at > stored.updated_at) stored.updated_at = event.created_at;
    stored.cursor = event.id;
  }
  // 侧栏摘要不需要轮次明细，完整任务状态保存在任务行。
  await storage().save(scope, {
    work: { ...stored, job: { ...stored.job, rounds: [] } },
    messages,
    jobs: [...jobs.values()],
  });
  return stored;
}
/**
 * 用服务端报告的任务状态校正本机仍停在排队或运行的最新任务：事件已被服务端过期清理（长时间离线）或确认丢失时，
 * 终态事件再也不会送达，会话会一直被锁在运行中。只改任务状态与成功版本指针，已过期的消息不会补回；
 * 返回是否改动了本地记录。其他任务、本机已是终态或状态相同时不做任何事。
 */
export function reconcile(job: Job): Promise<boolean> {
  return serial(job.project_id, async () => {
    const scope = server();
    const stored = await storage().work(scope, job.project_id);
    if (!stored || stored.job.id !== job.id) return false;
    const active = (status: Job["status"]) => status === "queued" || status === "running";
    if (!active(stored.job.status) || stored.job.status === job.status) return false;
    const settled: SessionJob = { ...stored.job, ...job };
    const pointer =
      job.status === "succeeded" && job.result_version_id ? job.result_version_id : stored.work.current_version_id;
    await storage().save(scope, {
      work: {
        ...stored,
        work: { ...stored.work, current_version_id: pointer },
        job: { ...settled, rounds: [] },
        updated_at: new Date().toISOString(),
      },
      messages: [],
      jobs: [settled],
    });
    return true;
  });
}
/** 服务端报告会话不存在或正在清理时标记本地记录，侧栏据此提供重试删除；本机没有该会话时不做任何事。 */
export function markDeleting(work: string): Promise<void> {
  return serial(work, async () => {
    const scope = server();
    const stored = await storage().work(scope, work);
    if (stored && !stored.deleting)
      await storage().save(scope, { work: { ...stored, deleting: true }, messages: [], jobs: [] });
  });
}
/** 删除当前服务下本机会话的全部记录；其他服务的同 ID 记录不受影响，服务端数据由删除接口单独清理。 */
export function forget(work: string): Promise<void> {
  return serial(work, () => storage().remove(server(), work));
}
