/**
 * 字效会话的本地存储：聊天消息、任务公开状态与本机设备 ID 只保存在使用者这一端；版本列表与代码按需从服务端读取。
 *
 * 桌面通过 Tauri `remotion_sessions` 命令读写应用数据目录下的 SQLite；浏览器仅供本地测试，使用页面内存，
 * 刷新即清空。两种实现共用同一存储接口，事件合并规则只在本文件实现一次：会话订阅把服务端事件交给
 * `apply` 写入本地后，再向服务端确认送达，服务端随即删除已送达的事件。
 */
import { invoke, isTauri } from "@tauri-apps/api/core";
import { ApiError } from "./api";
import type {
  ChatMessage,
  Job,
  SessionJob,
  SessionSnapshot,
  WorkEvent,
  WorkPage,
  WorkSummary,
} from "./model";

/** 一条本地会话：作品指针、最新任务、侧栏标题、最后活动时间与已写入的最后事件编号。 */
export interface StoredWork {
  work: { id: string; current_version_id: string | null };
  title: string;
  job: SessionJob;
  updated_at: string;
  cursor: number;
}
/** 一次原子写入：会话行与本批新增或更新的消息、任务。 */
export interface SessionChange {
  work: StoredWork;
  messages: ChatMessage[];
  jobs: SessionJob[];
}
/** 本地存储后端；messages 按序号从新到旧返回，`before` 为 null 时从最新开始。 */
export interface SessionStorage {
  device(): Promise<string>;
  works(): Promise<StoredWork[]>;
  work(id: string): Promise<StoredWork | null>;
  messages(work: string, before: number | null, limit: number): Promise<ChatMessage[]>;
  jobs(work: string, ids: string[]): Promise<SessionJob[]>;
  save(change: SessionChange): Promise<void>;
  remove(work: string): Promise<void>;
}

/** 桌面实现：每个操作是一次 Rust 侧 SQLite 调用，保存在一个事务内完成。 */
function tauriStorage(): SessionStorage {
  /** 统一调用入口；Rust 返回的中文错误原样展示。 */
  function call<T>(operation: string, payload: Record<string, unknown> = {}) {
    return invoke<T>("remotion_sessions", { operation, payload });
  }
  return {
    device: () => call("device"),
    works: () => call("works"),
    work: (id) => call("work", { id }),
    messages: (work, before, limit) => call("messages", { work, before, limit }),
    jobs: (work, ids) => call("jobs", { work, ids }),
    save: (change) => call("save", { ...change }),
    remove: (work) => call("remove", { work }),
  };
}

/** 浏览器测试实现：页面内存保存，刷新后设备 ID 与会话一起丢弃。 */
export function memoryStorage(): SessionStorage {
  const device = crypto.randomUUID();
  const works = new Map<string, StoredWork>();
  const messages = new Map<string, { work: string; message: ChatMessage }>();
  const jobs = new Map<string, SessionJob>();
  return {
    device: async () => device,
    works: async () =>
      [...works.values()].sort((left, right) =>
        left.updated_at === right.updated_at
          ? right.work.id.localeCompare(left.work.id)
          : right.updated_at.localeCompare(left.updated_at),
      ),
    work: async (id) => structuredClone(works.get(id) ?? null),
    messages: async (work, before, limit) =>
      [...messages.values()]
        .flatMap((item) => (item.work === work ? [structuredClone(item.message)] : []))
        .filter((item) => before === null || (item.sequence ?? 0) < before)
        .sort((left, right) => (right.sequence ?? 0) - (left.sequence ?? 0))
        .slice(0, limit),
    jobs: async (work, ids) =>
      ids.flatMap((id) => {
        const job = jobs.get(id);
        return job && job.project_id === work ? [structuredClone(job)] : [];
      }),
    save: async (change) => {
      works.set(change.work.work.id, structuredClone(change.work));
      for (const job of change.jobs) jobs.set(job.id, structuredClone(job));
      for (const message of change.messages)
        messages.set(message.id, { work: change.work.work.id, message: structuredClone(message) });
    },
    remove: async (work) => {
      works.delete(work);
      for (const [id, job] of jobs) if (job.project_id === work) jobs.delete(id);
      for (const [id, message] of messages) if (message.work === work) messages.delete(id);
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

/** 侧栏摘要只包含标题、活动时间与最新任务。 */
function summary(item: StoredWork): WorkSummary {
  return {
    id: item.work.id,
    title: item.title,
    updated_at: item.updated_at,
    current_version_id: item.work.current_version_id,
    job: item.job,
  };
}
/** 本机全部会话按最近活动排序；本地数量有限，不分页。 */
export async function history(): Promise<WorkPage> {
  return { items: (await storage().works()).map(summary), next_cursor: null };
}
/** 从本地恢复与原服务端快照相同的结构；本机没有该会话时视为不存在。 */
export async function snapshot(work: string, before?: number, limit = 50): Promise<SessionSnapshot> {
  const stored = await storage().work(work);
  if (!stored) throw new ApiError(404, "会话不存在于本机。");
  const rows = await storage().messages(work, before ?? null, limit + 1);
  const messages = rows.slice(0, limit).reverse();
  const ids = [...new Set([...messages.flatMap((item) => (item.job_id ? [item.job_id] : [])), stored.job.id])];
  const jobs = await storage().jobs(work, ids);
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
export async function record(
  project: { id: string; current_version_id: string | null; created_at: string; request: { description?: string | null } },
  job: Job,
) {
  const initial: SessionJob = { ...job, created_at: project.created_at, updated_at: project.created_at, parameters: null };
  await storage().save({
    work: {
      work: { id: project.id, current_version_id: project.current_version_id },
      title: (project.request.description || "图片字效").slice(0, 40),
      job: initial,
      updated_at: project.created_at,
      cursor: 0,
    },
    messages: [],
    jobs: [initial],
  });
}
/** 提交消息、参数或重试后先登记服务端返回的新任务为最新任务，事件到达前界面也不会回到旧任务状态。 */
export async function track(job: Job) {
  const stored = await storage().work(job.project_id);
  if (!stored || stored.job.id === job.id) return;
  const stamp = new Date().toISOString();
  const latest: SessionJob = { ...job, created_at: stamp, updated_at: stamp, parameters: null };
  await storage().save({ work: { ...stored, job: latest }, messages: [], jobs: [latest] });
}
/**
 * 按事件编号顺序把一批服务端事件合并进本地会话并原子保存，返回保存后的会话。
 *
 * 状态事件不含轮次，合并时保留已有轮次；轮次按层级与回合去重；成功版本只更新作品指针。
 * 已写入（编号不大于游标）的事件被忽略，重复投递不会产生重复记录。
 */
export async function apply(work: string, events: WorkEvent[]): Promise<StoredWork> {
  const stored = await storage().work(work);
  if (!stored) throw new ApiError(404, "会话不存在于本机。");
  const fresh = events.filter((event) => event.id > stored.cursor).sort((left, right) => left.id - right.id);
  if (!fresh.length) return stored;
  const wanted = fresh.flatMap((event) =>
    event.type === "job.updated" ? [event.data.id] : event.type === "job.round" ? [event.data.job_id] : [],
  );
  const jobs = new Map((await storage().jobs(work, [...new Set(wanted)])).map((job) => [job.id, job]));
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
  await storage().save({ work: { ...stored, job: { ...stored.job, rounds: [] } }, messages, jobs: [...jobs.values()] });
  return stored;
}
/** 删除本机会话的全部记录；服务端数据由删除接口单独清理。 */
export function forget(work: string): Promise<void> {
  return storage().remove(work);
}
