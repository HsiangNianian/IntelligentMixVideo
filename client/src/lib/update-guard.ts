/** 更新应用前的页面门禁：原表单中的草稿与进行中的请求。 */
import { useLayoutEffect, useRef } from 'react';
import { diagnosticContext, diagnosticInvoke as invoke, inDiagnosticContext, debugLog } from './diagnostics';
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
/** 表单在自己的输入控件上标出未保存项，隐藏的工作区仍挂载，清单可直接定位原控件。 */
export function updateDrafts() {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-update-draft]')).filter(field =>
    // 模板数字输入允许暂存无效文本，此时已解析的模板值可能与保存时完全一致。
    !field.hasAttribute('data-update-invalid-only') || Array.from(field.closest('form')?.querySelectorAll('input') ?? []).some(input => input.willValidate && !input.validity.valid)
  );
}
/** 登记的在途操作自动等待，只有实际存在的草稿需要用户处理。 */
export function updateBlockers(): string[] {
  return [...new Set([
    ...blockers.values(),
    ...(requests.size ? ['正在完成保存、上传或读取请求'] : []),
    ...updateDrafts().map(field => field.dataset.updateDraft!),
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
  const context = diagnosticContext();
  if (locked) debugLog('前端执行', `${context ? `[#${context}] ` : ''}等待客户端更新解除请求锁`, '信息');
  while (locked) await unlocked;
  const request = Symbol();
  requests.add(request);
  try { return await inDiagnosticContext(context, task); } finally { requests.delete(request); }
}
/** 业务接口复用原 fetch/invoke，更新接口本身不经过门禁。 */
export const updateFetch = (...args: Parameters<typeof fetch>) => updateTask(() => globalThis.fetch(...args));
export const updateInvoke: typeof invoke = (...args) => updateTask(() => invoke(...args));

/** 测试销毁门禁；旧请求结束时只移除自己的标记，不影响重置后的新请求。 */
export function resetUpdateGuard() {
  blockers.clear(); requests.clear(); lockForUpdate(false);
}
