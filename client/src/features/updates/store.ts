/** 与设置弹窗生命周期分离的单一更新流程；只在用户点击之后下载和等待应用。 */
import { toast } from 'sonner';
import { useSyncExternalStore } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { APP_VERSION } from '@/lib/version';
import { lockForUpdate, resetUpdateGuard, updateBlockers } from '@/lib/update-guard';
import { syncBackground } from '@/features/remotion_templates/backgroundSync';
import { recovery, restoreRecovery, type Recovery } from './recovery';
export type Candidate = { version: string; mode: 'hot' | 'native' | 'manual' };
type Phase = 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'waiting' | 'applying' | 'error' | 'manual';
type State = { phase: Phase; nativeVersion: string; candidate?: Candidate; message: string; progress?: number; links?: string[]; sha256?: string };
let state: State = { phase: 'disabled', nativeVersion: APP_VERSION, message: '此构建不支持客户端更新' };
const listeners = new Set<() => void>();
let initialization: Promise<void> | undefined;
let ready = false;
let timer: ReturnType<typeof setInterval> | undefined;
let applying = false;
let stopSync: (() => void) | undefined;
function stopWaiting() { if (timer) clearInterval(timer); timer = undefined; stopSync?.(); stopSync = undefined; }
let generation = 0;
let unlisten: (() => void) | undefined;
function publish(next: Partial<State>) { state = { ...state, ...next }; listeners.forEach(fn => fn()); }
function call<T>(operation: string, payload?: unknown) { return invoke<T>('client_update', { operation, payload }); }
export function useClientUpdate() { return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => state); }
export async function initializeUpdates() {
  if (!isTauri() || import.meta.env.IMV_DEBUG === 'true') return;
  initialization ??= (async () => {
    try {
      const info = await call<{ enabled: boolean; nativeVersion: string; notice: string; resume: Recovery | null }>('status');
      if (!info?.enabled) return;
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
export async function updaterReady() {
  if (state.phase === 'disabled' || ready) return;
  ready = true;
  try { await call('ready', { version: APP_VERSION }); } catch (e) { publish({ message: String(e) }); return; }
  void checkUpdate();
}
export async function checkUpdate() {
  if (['disabled', 'checking', 'downloading', 'waiting', 'applying'].includes(state.phase)) return;
  publish({ phase: 'checking', message: '正在检查更新…', progress: undefined, links: undefined });
  try {
    const candidate = await call<Candidate | null>('check');
    publish({ phase: candidate ? 'available' : 'idle', candidate: candidate ?? undefined, message: candidate ? `发现新版本 v${candidate.version}` : '当前已是可用的最新版本' });
  } catch (e) { publish({ phase: 'error', message: String(e) }); }
}
async function pendingTasks(): Promise<number> {
  return invoke<number>('remotion_sessions', { operation: 'pending', payload: {} });
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
    if (pending) { publish({ message: '等待后台任务完成；状态不明或正在删除的会话也需先处理' }); return; }
    lockForUpdate(true);
    // 锁住新操作后重新核对，防止前一次异步读取期间用户又修改草稿或提交任务。
    if (updateBlockers().length || await pendingTasks()) { lockForUpdate(false); return; }
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
export async function downloadUpdate() {
  if (!state.candidate || !['available', 'error', 'manual'].includes(state.phase)) return;
  const request = ++generation;
  publish({ phase: 'downloading', message: '正在下载并校验更新…', progress: 0, links: undefined });
  try {
    const result = await call<{ links?: string[]; sha256?: string } | null>('download');
    if (result?.links) { publish({ phase: 'manual', links: result.links, sha256: result.sha256, progress: undefined, message: '此版本需要完整升级，请按顺序选择下载源安装 deb 包' }); return; }
    publish({ phase: 'waiting', message: '下载完成，等待安全应用更新', progress: undefined });
    stopWaiting();
    const controller = new AbortController();
    stopSync = () => controller.abort();
    let syncing = false;
    // 覆盖没有打开字效工作区的情况；复用串行事件合并，网络失败不把任务当作完成。
    const sync = async (settle = false) => {
      if (syncing) return;
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
export function cancelUpdate() {
  if (state.phase !== 'waiting') return;
  generation++; stopWaiting();
  lockForUpdate(false);
  publish({ phase: 'available', message: '已取消此次应用，当前版本保持不变' });
}
/** 测试销毁单例，避免模拟桌面状态跨用例残留。 */
export function resetUpdates() {
  generation++; stopWaiting(); unlisten?.(); unlisten = undefined;
  initialization = undefined; ready = false; applying = false; resetUpdateGuard(); restoreRecovery(null);
  publish({ phase: 'disabled', nativeVersion: APP_VERSION, message: '此构建不支持客户端更新', candidate: undefined, progress: undefined, links: undefined });
}
