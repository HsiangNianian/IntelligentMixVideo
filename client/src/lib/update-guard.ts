/** 更新应用前的页面门禁：草稿、进行中的请求以及仍打开的业务对话框。 */
import { useLayoutEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
const blockers = new Map<symbol, string>();
let locked = false;
let requests = 0;
export function useUpdateBlocker(reason: string, blocked: boolean) {
  const key = useRef(Symbol());
  useLayoutEffect(() => {
    const id = key.current;
    if (blocked) blockers.set(id, reason);
    else blockers.delete(id);
    return () => { blockers.delete(id); };
  }, [reason, blocked]);
}
export function updateBlockers(): string[] {
  return [...new Set([
    ...blockers.values(),
    ...(requests ? ['正在完成保存、上传或读取请求'] : []),
    ...(document.querySelector('[data-slot="dialog-content"][data-state="open"]:not([data-update-safe])') ? ['请先完成或关闭其他对话框'] : []),
  ])];
}
function stopInput(event: Event) { event.preventDefault(); event.stopImmediatePropagation(); }
/** 在最终核查前禁止新交互，核查失败立刻解锁；不会锁住用户保存草稿的等待阶段。 */
export function lockForUpdate(value: boolean) {
  locked = value;
  for (const name of ['pointerdown', 'click', 'keydown', 'beforeinput', 'submit', 'drop', 'paste']) {
    if (value) window.addEventListener(name, stopInput, true);
    else window.removeEventListener(name, stopInput, true);
  }
}
export async function updateTask<T>(task: () => Promise<T>): Promise<T> {
  if (locked) throw new Error('正在应用客户端更新，请稍候');
  requests++;
  try { return await task(); } finally { requests--; }
}
/** 业务接口复用原 fetch/invoke，更新接口本身不经过门禁。 */
export const updateFetch = (...args: Parameters<typeof fetch>) => updateTask(() => globalThis.fetch(...args));
export const updateInvoke: typeof invoke = (...args) => updateTask(() => invoke(...args));
