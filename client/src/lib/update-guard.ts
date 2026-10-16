/** 更新应用前的页面门禁：草稿、进行中的请求以及仍打开的业务对话框。 */
import { useLayoutEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
const blockers = new Map<symbol, string>();
let locked = false;
/** 锁定期间发起的请求在此等待；解锁（应用失败或取消）时放行，应用成功则页面刷新、请求不再发出。 */
let unlocked: Promise<void> = Promise.resolve();
let release = () => {};
const requests = new Set<symbol>();
/** 组件处于 `blocked` 时登记等待原因，卸载或解除时移除；用于未保存草稿等页面内状态。 */
export function useUpdateBlocker(reason: string, blocked: boolean) {
  const key = useRef(Symbol());
  useLayoutEffect(() => {
    const id = key.current;
    if (blocked) blockers.set(id, reason);
    else blockers.delete(id);
    return () => { blockers.delete(id); };
  }, [reason, blocked]);
}
/** 当前阻止应用更新的原因（去重）：登记的草稿、进行中的请求和未标记为安全的打开对话框。 */
export function updateBlockers(): string[] {
  return [...new Set([
    ...blockers.values(),
    ...(requests.size ? ['正在完成保存、上传或读取请求'] : []),
    ...(document.querySelector('[data-slot="dialog-content"][data-state="open"]:not([data-update-safe])') ? ['请先完成或关闭其他对话框'] : []),
  ])];
}
/** 捕获阶段吞掉用户输入，锁定期间不能开始新操作。 */
function stopInput(event: Event) { event.preventDefault(); event.stopImmediatePropagation(); }
/** 在最终核查前禁止新交互，核查失败立刻解锁；不会锁住用户保存草稿的等待阶段。 */
export function lockForUpdate(value: boolean) {
  if (value && !locked) unlocked = new Promise(resolve => { release = resolve; });
  if (!value && locked) release();
  locked = value;
  for (const name of ['pointerdown', 'click', 'keydown', 'beforeinput', 'submit', 'drop', 'paste']) {
    if (value) window.addEventListener(name, stopInput, true);
    else window.removeEventListener(name, stopInput, true);
  }
}
/**
 * 执行一次业务请求并在完成前登记为进行中。锁定期间不开始新请求而是等待解锁：
 * 后台写入（如 SSE 事件落库）因此不会失败，应用更新失败解锁后照常继续。
 */
export async function updateTask<T>(task: () => Promise<T>): Promise<T> {
  while (locked) await unlocked;
  const request = Symbol();
  requests.add(request);
  try { return await task(); } finally { requests.delete(request); }
}
/** 业务接口复用原 fetch/invoke，更新接口本身不经过门禁。 */
export const updateFetch = (...args: Parameters<typeof fetch>) => updateTask(() => globalThis.fetch(...args));
export const updateInvoke: typeof invoke = (...args) => updateTask(() => invoke(...args));

/** 测试销毁门禁；旧请求结束时只移除自己的标记，不影响重置后的新请求。 */
export function resetUpdateGuard() {
  blockers.clear(); requests.clear(); lockForUpdate(false);
}
