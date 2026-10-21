/** 与设置弹窗生命周期分离的单一更新流程；只在用户点击之后下载和等待应用。 */
import { toast } from 'sonner';
import { useSyncExternalStore } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { APP_VERSION } from '@/lib/version';
import { lockForUpdate, resetUpdateGuard, updateBlockers } from '@/lib/update-guard';
import { backgroundSyncActive, syncBackground } from '@/features/remotion_templates/backgroundSync';
import { recovery, restoreRecovery, type Recovery } from './recovery';
export type Candidate = { version: string; mode: 'hot' | 'native' | 'manual' };
/** 阻止应用更新的本地会话：所属服务地址、侧栏标题与是否等待重试删除。 */
type PendingWork = { server: string; title: string; deleting: boolean };
type Phase = 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'waiting' | 'applying' | 'error' | 'manual';
type State = { phase: Phase; nativeVersion: string; candidate?: Candidate; reminder?: Candidate; message: string; progress?: number; links?: string[]; sha256?: string };
let state: State = { phase: 'disabled', nativeVersion: APP_VERSION, message: '此构建不支持客户端更新' };
const listeners = new Set<() => void>();
let initialization: Promise<void> | undefined;
let ready = false;
/** 当前这一轮页面确认；重置后旧的重试循环据此停止。 */
let readyRun: symbol | undefined;
/** 页面确认的重试次数与间隔：总计约 40 秒，赶在原生 60 秒看门狗回退之前。 */
const READY_ATTEMPTS = 20;
const READY_RETRY_MS = 2000;
let timer: ReturnType<typeof setInterval> | undefined;
let applying = false;
let stopSync: (() => void) | undefined;
/** 停止等待阶段的定时核查与后台同步。 */
function stopWaiting() { if (timer) clearInterval(timer); timer = undefined; stopSync?.(); stopSync = undefined; }
let generation = 0;
let unlisten: (() => void) | undefined;
/** 合并状态并通知订阅组件。 */
function publish(next: Partial<State>) { state = { ...state, ...next }; listeners.forEach(fn => fn()); }
/** 调用 Rust 更新命令；前端只能指定操作名与恢复位置。 */
function call<T>(operation: string, payload?: unknown) { return invoke<T>('client_update', { operation, payload }); }
/** 订阅全局更新状态。 */
export function useClientUpdate() { return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => state); }
/** 页面挂载前读取一次更新状态：恢复更新前的位置、提示上次回退，并订阅下载进度；只执行一次。 */
export async function initializeUpdates() {
  if (!isTauri() || import.meta.env.IMV_DEBUG === 'true') return;
  initialization ??= (async () => {
    try {
      const info = await call<{ enabled: boolean; nativeVersion: string; notice: string; resume: Recovery | null }>('status');
      if (!info?.enabled) {
        // 更新存储不可用时客户端照常启动，只说明本次无法更新。
        if (info?.notice) { toast.warning(info.notice); publish({ message: info.notice }); }
        return;
      }
      restoreRecovery(info.resume);
      if (info.notice) toast.info(info.notice);
      publish({ phase: 'idle', nativeVersion: info.nativeVersion, message: info.notice || '尚未检查更新' });
      unlisten = await listen<{ received: number; total: number }>('client-update-progress', ({ payload }) => {
        if (state.phase === 'downloading') publish({ progress: Math.min(100, Math.round(payload.received / payload.total * 100)) });
      });
    } catch { publish({ message: '更新服务暂不可用，当前客户端仍可正常使用' }); }
  })();
  return initialization;
}
/**
 * 页面挂载后向原生层确认当前界面可用，清除热更新的待确认标记，成功后自动检查一次更新。
 * 确认失败（多为与检查更新争用操作锁）时重试，不能因一次失败让看门狗回退一个正常的版本。
 */
export async function updaterReady() {
  if (state.phase === 'disabled' || ready) return;
  ready = true;
  const run = Symbol();
  readyRun = run;
  for (let attempt = 1; ; attempt++) {
    try { await call('ready', { version: APP_VERSION }); break; } catch (e) {
      if (readyRun !== run) return;
      if (attempt >= READY_ATTEMPTS) { publish({ message: String(e) }); return; }
      await new Promise(resolve => setTimeout(resolve, READY_RETRY_MS));
      if (readyRun !== run) return;
    }
  }
  if (readyRun === run) void checkUpdate(true);
}
/**
 * 检查是否有新版本；忙碌或未启用时忽略，失败显示原因而不是「已是最新」。
 * 仅启动后的自动检查传入 `remind`，设置中的手动检查不弹窗。
 */
export async function checkUpdate(remind = false) {
  if (['disabled', 'checking', 'downloading', 'waiting', 'applying'].includes(state.phase)) return;
  // 手动检查只有在结果没有候选、或用户已经关闭提醒时才清除提醒；自动检查负责重建提醒。
  publish({ phase: 'checking', message: '正在检查更新…', progress: undefined, links: undefined, reminder: remind ? undefined : state.reminder });
  try {
    const candidate = await call<Candidate | null>('check');
    publish({ phase: candidate ? 'available' : 'idle', candidate: candidate ?? undefined, reminder: candidate && (remind || state.reminder) ? candidate : undefined, message: candidate ? `发现新版本 v${candidate.version}` : '当前已是可用的最新版本' });
  } catch (e) { publish({ phase: 'error', message: String(e), reminder: remind ? undefined : state.reminder }); }
}
/** 关闭启动提醒；只影响本次运行，下次启动发现新版本仍会提醒。 */
export function dismissUpdateReminder() {
  if (state.reminder) publish({ reminder: undefined });
}
/** 读取本机所有服务下未结束、状态不明或待重试删除的字效会话。 */
async function pendingTasks(): Promise<PendingWork[]> {
  return invoke<PendingWork[]>('remotion_sessions', { operation: 'pending', payload: {} });
}
/** 说明是哪些会话在阻止更新，以及如何处理。 */
function describePending(works: PendingWork[]) {
  const names = works.slice(0, 3).map(work => `「${work.title || '未命名会话'}」（${work.server}${work.deleting ? '，待重试删除' : ''}）`);
  return `等待后台会话结束：${names.join('、')}${works.length > 3 ? ` 等 ${works.length} 个` : ''}。其他服务地址下的会话需切换回该地址才能同步，也可以取消此次更新`;
}
/** 定时只读取本机任务状态；后台事件使用已有同步器，不新增任务状态轮询。 */
export async function tryApplyUpdate() {
  if (state.phase !== 'waiting' || applying) return;
  applying = true;
  const request = generation;
  try {
    const blocked = updateBlockers();
    if (blocked.length) { publish({ message: `等待处理：${blocked.join('；')}` }); return; }
    const pending = await pendingTasks();
    if (request !== generation || state.phase !== 'waiting') return;
    if (pending.length) { publish({ message: describePending(pending) }); return; }
    lockForUpdate(true);
    // 锁住新操作后重新核对，防止前一次异步读取期间用户又修改草稿或提交任务。
    if (updateBlockers().length || (await pendingTasks()).length) { lockForUpdate(false); return; }
    if (request !== generation || state.phase !== 'waiting') { lockForUpdate(false); return; }
    publish({ phase: 'applying', message: state.candidate?.mode === 'hot' ? '正在应用界面更新…' : '正在重新启动客户端…' });
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await call('apply', recovery);
    stopWaiting();
  } catch (e) {
    lockForUpdate(false);
    if (request === generation) publish({ phase: (state as State).phase === 'applying' ? 'error' : 'waiting', message: String(e) });
  } finally { applying = false; }
}
/** 下载并校验候选版本，随后进入等待：每秒核查草稿、请求与后台会话，全部就绪才应用。deb 只返回手动下载链接。 */
export async function downloadUpdate() {
  if (!state.candidate || !['available', 'error', 'manual'].includes(state.phase)) return;
  const request = ++generation;
  publish({ phase: 'downloading', message: '正在下载并校验更新…', progress: 0, links: undefined });
  try {
    const result = await call<{ links?: string[]; sha256?: string } | null>('download');
    if (result?.links) { publish({ phase: 'manual', links: result.links, sha256: result.sha256, progress: undefined, message: '此版本需要完整升级，请按顺序选择下载源安装 deb 包' }); return; }
    publish({ phase: 'waiting', message: '下载完成，等待安全应用更新', progress: undefined, reminder: undefined });
    stopWaiting();
    const controller = new AbortController();
    stopSync = () => controller.abort();
    let syncing = false;
    // 只覆盖没有打开字效工作区的情况：工作区挂载后由它同步并跳过 SSE 会话，这里不重复拉取或确认。
    // 复用串行事件合并，网络失败不把任务当作完成。
    const sync = async (settle = false) => {
      if (syncing || backgroundSyncActive()) return;
      syncing = true;
      try { await syncBackground(() => false, controller.signal, settle); } catch {} finally { syncing = false; }
    };
    void sync(true);
    let ticks = 0;
    timer = setInterval(() => {
      if (request !== generation || !['waiting', 'applying'].includes(state.phase)) { stopWaiting(); return; }
      if (++ticks % 30 === 0) void sync();
      void tryApplyUpdate();
    }, 1000);
    void tryApplyUpdate();
  } catch (e) { publish({ phase: 'error', message: String(e), progress: undefined }); }
}
/** 取消等待中的这次应用：保留已下载结果可再次点击，不影响业务任务。 */
export function cancelUpdate() {
  if (state.phase !== 'waiting') return;
  generation++; stopWaiting();
  lockForUpdate(false);
  publish({ phase: 'available', message: '已取消此次应用，当前版本保持不变' });
}
/** 测试销毁单例，避免模拟桌面状态跨用例残留。 */
export function resetUpdates() {
  generation++; stopWaiting(); unlisten?.(); unlisten = undefined;
  initialization = undefined; ready = false; readyRun = undefined; applying = false; resetUpdateGuard(); restoreRecovery(null);
  publish({ phase: 'disabled', nativeVersion: APP_VERSION, message: '此构建不支持客户端更新', candidate: undefined, reminder: undefined, progress: undefined, links: undefined });
}
