/** 与设置弹窗生命周期分离的单一更新流程；只在用户点击之后下载和等待应用。 */
import { toast } from 'sonner';
import { useSyncExternalStore } from 'react';
import { isTauri } from '@tauri-apps/api/core';
import { beginDiagnostic, diagnosticInvoke as invoke, inDiagnosticContext, type DiagnosticTrace } from '@/lib/diagnostics';
import { listen } from '@tauri-apps/api/event';
import { APP_VERSION } from '@/lib/version';
import { lockForUpdate, resetUpdateGuard, updateBlockers } from '@/lib/update-guard';
import { backgroundSyncActive, syncBackground } from '@/features/remotion_templates/backgroundSync';
import { registrationBlockers } from '@/features/remotion_templates/localSessions';
import { recovery, restoreRecovery, type Recovery } from './recovery';
export type Candidate = { version: string; mode: 'hot' | 'native' | 'manual' };
/** 阻止应用更新的本地会话：所属服务地址、侧栏标题与是否等待重试删除。 */
type PendingWork = { server: string; title: string; deleting: boolean };
type Phase = 'disabled' | 'idle' | 'checking' | 'available' | 'preparing' | 'downloading' | 'waiting' | 'applying' | 'error' | 'manual';
type State = { phase: Phase; candidate?: Candidate; reminder?: Candidate; message: string; progress?: number; links?: string[]; sha256?: string };
let state: State = { phase: 'disabled', message: '此构建不支持客户端更新' };
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
/** 同一轮预检只执行一次；取消后新一轮不被旧回执覆盖。 */
let preparation: number | undefined;
let stopSync: (() => void) | undefined;
/** 停止等待阶段的定时核查与后台同步。 */
function stopWaiting() { if (timer) clearInterval(timer); timer = undefined; stopSync?.(); stopSync = undefined; }
let generation = 0;
let unlisten: (() => void) | undefined;
/** 单一更新流程显式持有编号；经过等待、轮询和其他点击后仍属于原更新操作。 */
let trace: DiagnosticTrace | undefined;
/** 合并状态并通知订阅组件。 */
function publish(next: Partial<State>) {
  const previous = state;
  state = { ...state, ...next };
  if (state.phase !== previous.phase || state.message !== previous.message)
    trace?.step(`状态 ${state.phase}：${state.message}`, state.phase === 'error' ? '错误' : '信息');
  else if (state.progress !== undefined && Math.floor(state.progress / 10) !== Math.floor((previous.progress ?? -10) / 10))
    trace?.step(`下载进度 ${state.progress}%`);
  listeners.forEach(fn => fn());
}
/** 调用 Rust 更新命令；前端只能指定操作名与恢复位置。 */
function call<T>(operation: string, payload?: unknown) {
  return inDiagnosticContext(trace?.id, () => invoke<T>('client_update', { operation, payload }));
}
/** 订阅全局更新状态。 */
export function useClientUpdate() { return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => state); }
/** 页面挂载前读取一次更新状态：恢复更新前的位置、提示上次回退，并订阅下载进度；只执行一次。 */
export async function initializeUpdates() {
  if (!isTauri() || import.meta.env.IMV_DEBUG === 'true') return;
  initialization ??= (async () => {
    trace = beginDiagnostic('前端更新', '初始化更新服务');
    try {
      const info = await call<{ enabled: boolean; notice: string; resume: Recovery | null }>('status');
      if (!info?.enabled) {
        // 更新存储不可用时客户端照常启动，只说明本次无法更新。
        if (info?.notice) { toast.warning(info.notice); publish({ message: info.notice }); }
        trace.finish(info?.notice || '此构建未启用更新');
        return;
      }
      restoreRecovery(info.resume);
      if (info.notice) toast.info(info.notice);
      publish({ phase: 'idle', message: info.notice || '尚未检查更新' });
      unlisten = await listen<{ received: number; total: number }>('client-update-progress', ({ payload }) => {
        if (state.phase === 'downloading') publish({ progress: Math.min(100, Math.round(payload.received / payload.total * 100)) });
      });
      trace.finish('更新状态已恢复，进度监听已建立');
    } catch (error) {
      trace.finish(`初始化失败：${String(error)}`, '错误');
      publish({ message: '更新服务暂不可用，当前客户端仍可正常使用' });
    }
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
  if (['disabled', 'checking', 'preparing', 'downloading', 'waiting', 'applying'].includes(state.phase)) {
    const skipped = beginDiagnostic('前端更新', '检查更新');
    skipped.finish(`未执行：当前状态 ${state.phase}；${state.message}`);
    return;
  }
  trace = beginDiagnostic('前端更新', remind ? '启动检查更新' : '检查更新');
  // 手动检查只有在结果没有候选、或用户已经关闭提醒时才清除提醒；自动检查负责重建提醒。
  publish({ phase: 'checking', message: '正在检查更新…', progress: undefined, links: undefined, reminder: remind ? undefined : state.reminder });
  try {
    const candidate = await call<Candidate | null>('check');
    publish({ phase: candidate ? 'available' : 'idle', candidate: candidate ?? undefined, reminder: candidate && (remind || state.reminder) ? candidate : undefined, message: candidate ? `发现新版本 v${candidate.version}` : '当前已是可用的最新版本' });
    trace.finish(candidate ? `检查完成：v${candidate.version}，更新方式 ${candidate.mode}` : '检查完成：没有更高的有效版本');
  } catch (e) {
    publish({ phase: 'error', message: String(e), reminder: remind ? undefined : state.reminder });
    trace.finish(`检查失败：${String(e)}`, '错误');
  }
}
/** 关闭启动提醒；只影响本次运行，下次启动发现新版本仍会提醒。 */
export function dismissUpdateReminder() {
  if (state.reminder) publish({ reminder: undefined });
}
/** 读取本机所有服务下未结束、状态不明或待重试删除的字效会话。 */
async function pendingTasks(): Promise<PendingWork[]> {
  return inDiagnosticContext(trace?.id, () => invoke<PendingWork[]>('remotion_sessions', { operation: 'pending', payload: {} }));
}
/** 说明是哪些会话在阻止更新，以及如何处理。 */
function describePending(works: PendingWork[]) {
  const names = works.slice(0, 3).map(work => `「${work.title || '未命名会话'}」（${work.server}${work.deleting ? '，待重试删除' : ''}）`);
  return `等待后台会话结束：${names.join('、')}${works.length > 3 ? ` 等 ${works.length} 个` : ''}。其他服务地址下的会话需切换回该地址才能同步，也可以取消此次更新`;
}
/** 两种自动更新都等待尚未落盘的任务回执，包括已关闭聊天及其他服务的回执。 */
async function blockers() {
  const registrations = await registrationBlockers();
  // 读取原生回执期间仍可能出现新输入或请求，所以最后读取页面门禁。
  return [...updateBlockers(), ...registrations];
}
/** 整包等待页面草稿、在途操作和未登记回执；热更新额外核查本机后台任务。 */
export async function tryApplyUpdate() {
  if (state.phase === 'preparing') return prepareDownload();
  if (state.phase !== 'waiting' || applying) return;
  applying = true;
  const request = generation;
  try {
    const blocked = await blockers();
    if (request !== generation || state.phase !== 'waiting') return;
    if (blocked.length) {
      lockForUpdate(false);
      publish({ message: `等待处理：${blocked.join('；')}` });
      return;
    }
    const hot = state.candidate?.mode === 'hot';
    const pending = hot ? await pendingTasks() : [];
    if (request !== generation || state.phase !== 'waiting') return;
    if (pending.length) { publish({ message: describePending(pending) }); return; }
    lockForUpdate(true);
    // 锁住新操作后重新核对，防止前一次异步读取期间用户又修改草稿或提交任务。
    const changed = (await blockers()).length || (hot && (await pendingTasks()).length);
    if (request !== generation || state.phase !== 'waiting') return;
    if (changed) { lockForUpdate(false); return; }
    publish({ phase: 'applying', message: state.candidate?.mode === 'hot' ? '正在应用界面更新…' : '正在重新启动客户端…' });
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await call('apply', recovery);
    trace?.finish('原生应用更新命令已返回；重载或重启后的页面将另行确认版本');
    stopWaiting();
  } catch (e) {
    if (request === generation) {
      lockForUpdate(false);
      publish({ phase: (state as State).phase === 'applying' ? 'error' : 'waiting', message: String(e) });
      if ((state as State).phase === 'error') trace?.finish(`应用失败：${String(e)}`, '错误');
    }
  } finally { applying = false; }
}
/** 用户只发起一次更新。先让各表单处理草稿，最后一个阻塞解除后自动下载。 */
export async function downloadUpdate() {
  if (!state.candidate || !['available', 'error', 'manual'].includes(state.phase)) return;
  trace = beginDiagnostic('前端更新', `更新到 v${state.candidate.version}（${state.candidate.mode}）`);
  ++generation;
  publish({ phase: 'preparing', message: '正在检查未保存的内容…', links: undefined });
  await prepareDownload();
}
/** 手动 deb 只取下载链接。可自动安装的更新先核查并锁定编辑，下载失败即解锁。 */
async function prepareDownload() {
  const request = generation;
  if (state.phase !== 'preparing' || preparation === request) return;
  preparation = request;
  try { await prepare(request); }
  catch (error) {
    if (request === generation) {
      lockForUpdate(false);
      publish({ phase: 'error', message: String(error) });
      trace?.finish(`下载前检查失败：${String(error)}`, '错误');
    }
  }
  finally { if (preparation === request) preparation = undefined; }
}
/** 异步核实回执后重查草稿，再锁定下载；迟到回执不恢复已取消的更新。 */
async function prepare(request: number) {
  const manual = state.candidate?.mode === 'manual';
  const blocked = manual ? [] : await blockers();
  if (request !== generation || state.phase !== 'preparing') return;
  if (blocked.length) {
    publish({ message: `等待处理：${blocked.join('；')}` });
    timer ??= setInterval(() => { void tryApplyUpdate(); }, 1000);
    return;
  }
  stopWaiting();
  if (!manual) lockForUpdate(true);
  publish({ phase: 'downloading', message: '正在下载并校验更新…', progress: 0, reminder: state.candidate });
  try {
    const result = await call<{ links?: string[]; sha256?: string } | null>('download');
    if (request !== generation) return;
    if (result?.links) {
      publish({ phase: 'manual', links: result.links, sha256: result.sha256, progress: undefined, message: '此版本需要完整升级，请按顺序选择下载源安装 deb 包' });
      trace?.finish('安装包链接与校验摘要已取得，等待用户手动安装');
      return;
    }
    // 热更新可能还要同步服务端状态，等待期间允许处理或取消。
    if (state.candidate?.mode === 'hot') lockForUpdate(false);
    publish({ phase: 'waiting', message: '下载完成，等待安全应用更新', progress: undefined, reminder: undefined });
    stopWaiting();
    const controller = new AbortController();
    stopSync = () => controller.abort();
    let syncing = false;
    // 只覆盖没有打开字效工作区的情况：工作区挂载后由它同步并跳过 SSE 会话，这里不重复拉取或确认。
    // 复用串行事件合并，网络失败不把任务当作完成。
    const sync = async (settle = false) => {
      if (state.candidate?.mode !== 'hot' || syncing || backgroundSyncActive()) return;
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
  } catch (e) {
    if (request !== generation) return;
    lockForUpdate(false);
    publish({ phase: 'error', message: String(e), progress: undefined });
    trace?.finish(`下载失败：${String(e)}`, '错误');
  }
}
/** 取消本次更新等待，原表单和已登记的业务任务继续使用。 */
export function cancelUpdate() {
  if (state.phase !== 'preparing' && state.phase !== 'waiting') return;
  generation++; stopWaiting();
  lockForUpdate(false);
  publish({ phase: 'available', reminder: undefined, message: '已取消此次应用，当前版本保持不变' });
  trace?.finish('用户取消更新等待');
}
/** 测试销毁单例，避免模拟桌面状态跨用例残留。 */
export function resetUpdates() {
  trace = undefined;
  generation++; stopWaiting(); unlisten?.(); unlisten = undefined;
  initialization = undefined; ready = false; readyRun = undefined; applying = false; resetUpdateGuard(); restoreRecovery(null);
  preparation = undefined;
  publish({ phase: 'disabled', message: '此构建不支持客户端更新', candidate: undefined, reminder: undefined, progress: undefined, links: undefined });
}
