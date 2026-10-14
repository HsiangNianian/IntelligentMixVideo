/**
 * 后台会话同步：只有当前打开的会话有 SSE 订阅，其余仍在排队或运行的本地会话由这里通过
 * `GET /works/{id}/events` 分页拉取待送达事件，按与 SSE 相同的合并规则写入本地后确认送达。
 *
 * 工作区挂载时与之后每 30 秒同步一次；SSE 正在订阅的会话跳过（写入前在该会话的本地队列里再次检查），
 * 服务端答复 404/410 时与打开中的会话一样标记为已删除或正在清理。网络或本地库失败留到下一轮重试。
 */
import { useEffect, useRef } from "react";
import * as api from "./api";
import * as local from "./localSessions";
import type { WorkEvent } from "./model";

/** 两轮同步之间的间隔。 */
export const SYNC_INTERVAL_MS = 30_000;
/** 一轮同步里单个会话最多读取的页数，异常响应不会造成死循环。 */
const MAX_PAGES = 50;
/** 与 SSE 相同，只写入这些公开事件类型。 */
const KNOWN = new Set<WorkEvent["type"]>(["message.created", "job.updated", "job.round", "version.ready"]);

/**
 * 本机最新任务仍在排队或运行时向服务端核对一次：事件已过期或确认丢失时，终态事件不会再送达（见 `local.reconcile`）。
 * 网络失败时保持原状，下次再核对；服务端答复 404/410 时抛出，由调用方按会话已删除处理；返回本地记录是否被校正。
 */
export async function settleJob(job: local.StoredWork["job"], signal?: AbortSignal): Promise<boolean> {
  if (job.status !== "queued" && job.status !== "running") return false;
  try {
    return await local.reconcile(await api.job(job.id, signal));
  } catch (error) {
    if (error instanceof api.ApiError && [404, 410].includes(error.status)) throw error;
    return false;
  }
}

/** 逐页拉取一个会话的待送达事件直到取空；返回是否写入了本地。会话转为 SSE 订阅时立即停下，不确认未写入的事件。 */
async function syncWork(stored: local.StoredWork, open: (id: string) => boolean, signal: AbortSignal, settle: boolean) {
  const work = stored.work.id;
  let cursor = stored.cursor;
  let changed = false;
  for (let page = 0; page < MAX_PAGES && !signal.aborted; page++) {
    const events = (await api.workEvents(work, cursor, signal))
      .filter((event) => event.work_id === work && Number.isSafeInteger(event.id) && event.id > cursor)
      .sort((left, right) => left.id - right.id);
    if (!events.length) {
      // 没有待送达事件而本机仍显示进行中：事件可能已过期，向服务端核对任务状态（只在挂载后的第一轮，不是轮询）。
      if (settle && page === 0 && (await settleJob(stored.job, signal))) changed = true;
      break;
    }
    const saved = await local.apply(
      work,
      events.filter((event) => KNOWN.has(event.type)),
      () => open(work) || signal.aborted,
    );
    if (!saved) break;
    changed = true;
    cursor = events.at(-1)!.id;
    // 确认失败不影响本地结果；事件留在服务端，下一次读取或 SSE 打开时再确认。
    await api.ack(work, cursor).catch(() => {});
  }
  return changed;
}

/**
 * 同步当前服务下全部未结束、未在 SSE 中打开的本地会话；返回侧栏是否需要刷新。
 * `settle` 为 true 时，无事件可取而仍显示进行中的会话再核对一次任务状态（工作区挂载后的第一轮）。
 */
export async function syncBackground(
  open: (id: string) => boolean,
  signal: AbortSignal,
  settle = false,
): Promise<boolean> {
  let changed = false;
  for (const stored of await local.pendingWorks()) {
    if (signal.aborted) break;
    if (open(stored.work.id)) continue;
    try {
      if (await syncWork(stored, open, signal, settle)) changed = true;
    } catch (error) {
      if (error instanceof api.ApiError && [404, 410].includes(error.status)) {
        await local.markDeleting(stored.work.id);
        changed = true;
      }
      // 其他失败（断网、超时、本地库或本地记录已删除）留给下一轮。
    }
  }
  return changed;
}

/** 工作区挂载期间定时同步后台会话；`open` 返回 SSE 正在订阅的会话，有写入时调用 `onChange` 刷新侧栏，卸载即停止。 */
export function useBackgroundSync(open: () => string | null, onChange: () => void) {
  const latest = useRef({ open, onChange });
  latest.current = { open, onChange };
  useEffect(() => {
    const controller = new AbortController();
    let running = false;
    /** 只有挂载后的第一轮核对任务状态：此后的停滞由事件或用户操作处理，不轮询任务接口。 */
    let first = true;
    /** 上一轮未结束时跳过本轮，避免同一会话的两轮同步重叠。 */
    async function tick() {
      if (running) return;
      running = true;
      try {
        const changed = await syncBackground((id) => latest.current.open() === id, controller.signal, first);
        first = false;
        if (changed && !controller.signal.aborted) latest.current.onChange();
      } catch {
        // 本地会话列表读取失败：侧栏自己会显示读取错误，下一轮重试。
      } finally {
        running = false;
      }
    }
    void tick();
    const timer = window.setInterval(() => void tick(), SYNC_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
}
