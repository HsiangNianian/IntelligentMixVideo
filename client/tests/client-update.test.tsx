/** 客户端更新流程测试：隔离 IPC 和网络，验证草稿、后台任务、重复点击、启动提醒及等待取消；不代替真实桌面安装测试。 */
import { expect, mock, test } from 'bun:test';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { mockDesktop } from './setup';
import { ClientUpdate, UpdateLifecycle } from '@/features/updates/ClientUpdate';
import { checkUpdate, downloadUpdate, initializeUpdates, cancelUpdate, tryApplyUpdate } from '@/features/updates/store';
import { useUpdateBlocker, updateTask, updateBlockers, lockForUpdate } from '@/lib/update-guard';
import { recovery } from '@/features/updates/recovery';
import { useBackgroundSync } from '@/features/remotion_templates/backgroundSync';
import { memoryStorage, setStorage } from '@/features/remotion_templates/localSessions';

type Pending = { server: string; title: string; deleting: boolean }[];
const OTHER_SERVER: Pending = [{ server: 'http://old.test', title: '旧服务会话', deleting: false }];

function desktop({ pending = [], fail = false, downloadFail = false, downloadGate, mode = 'hot', readyFailures = 0, status = { enabled: true, notice: '' } }: { pending?: Pending | Promise<Pending>; fail?: boolean; downloadFail?: boolean; downloadGate?: Promise<void>; mode?: string; readyFailures?: number; status?: { enabled: boolean; notice: string } } = {}) {
  const calls = mock(async (command: string, args: Record<string, unknown>) => {
    if (command === 'plugin:event|listen') return 1;
    if (command === 'remotion_sessions') return pending;
    if (command !== 'client_update') return null;
    if (args.operation === 'status') return { ...status, nativeVersion: '1.2.0', resume: null };
    if (args.operation === 'ready' && readyFailures-- > 0) throw new Error('另一个更新操作正在进行');
    if (args.operation === 'check') {
      if (fail) throw new Error('签名验证失败');
      return { version: '1.2.4', mode };
    }
    if (args.operation === 'download') {
      if (downloadGate) await downloadGate;
      if (downloadFail) throw new Error('下载失败：所有来源均不可用或文件校验失败，请重试');
      if (mode === 'manual') return { links: ['https://github.com/example/release.deb'], sha256: 'a'.repeat(64) };
    }
    return null;
  });
  mockDesktop(calls);
  return calls;
}
// 场景：隐藏工作区仍有草稿，下载完成后保持等待，处理完草稿才应用，并传递已保存位置。
test('下载不丢失草稿，安全后应用一次并恢复位置', async () => {
  const calls = desktop();
  await initializeUpdates();
  const blocker = renderHook(({ dirty }) => useUpdateBlocker('模板尚未保存', dirty), { initialProps: { dirty: true } });
  // 挂载会自动检查；在 act 内等其状态提交，不让 DOM 重试抢占异步检查的完成。
  await act(async () => { render(<ClientUpdate />); });
  expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy();
  expect(calls.mock.calls.filter(([, args]) => args.operation === 'check')).toHaveLength(1);
  await act(async () => { await downloadUpdate(); });
  await waitFor(() => expect(screen.getByText(/等待处理/)).toBeTruthy());
  expect(calls.mock.calls.some(([, args]) => args.operation === 'apply')).toBe(false);
  recovery.workspace = 'library'; recovery.template = { environment: 'cloud', templateId: 'saved' };
  blocker.rerender({ dirty: false });
  await act(async () => { await tryApplyUpdate(); });
  expect(calls.mock.calls.filter(([, args]) => args.operation === 'apply')).toHaveLength(1);
  expect(calls.mock.calls.find(([, args]) => args.operation === 'apply')?.[1].payload).toMatchObject({ workspace: 'library', template: { templateId: 'saved' } });
});
// 场景：当前页没有任务，但另一服务地址下的本机记录仍在运行；等待说明指出是哪个会话，取消等待不会安装或取消业务任务。
test('后台任务阻止应用并说明所属会话，取消等待不取消任务', async () => {
  const calls = desktop({ pending: OTHER_SERVER });
  await initializeUpdates();
  await act(async () => { render(<ClientUpdate />); });
  await act(async () => { await downloadUpdate(); await tryApplyUpdate(); });
  expect(screen.getByRole('status').textContent).toContain('「旧服务会话」（http://old.test）');
  cancelUpdate(); await tryApplyUpdate();
  expect(calls.mock.calls.some(([, args]) => ['apply', 'cancel'].includes(String(args.operation)))).toBe(false);
});
// 场景：网络或签名错误不能显示成“已是最新版”。
test('验证失败显示失败并允许重试', async () => {
  desktop({ fail: true }); await initializeUpdates();
  render(<ClientUpdate />);
  await waitFor(() => expect(screen.getByText(/签名验证失败/)).toBeTruthy());
  expect(screen.queryByText('当前已是可用的最新版本')).toBeNull();
  expect((screen.getByRole('button', { name: '检查更新' }) as HTMLButtonElement).disabled).toBe(false);
});
// 场景：整包更新在安装前显示自动重启说明，无第二轮确认。
test('整包更新显示重启状态对话框', async () => {
  desktop({ mode: 'native' }); await initializeUpdates();
  render(<UpdateLifecycle />);
  await act(async () => { await checkUpdate(); });
  await act(async () => { await downloadUpdate(); });
  await act(async () => { await tryApplyUpdate(); });
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('正在重新启动客户端'));
});
// 场景：正在上传时阻止刷新；进入应用阶段后新请求不开始，应用失败解锁后照常执行而不是报错（避免 SSE 落库失败停掉订阅）。
test('进行中请求参与门禁，锁定期间的新请求等到解锁再执行', async () => {
  let done!: () => void;
  const task = updateTask(() => new Promise<void>(resolve => { done = resolve; }));
  expect(updateBlockers()).toContain('正在完成保存、上传或读取请求');
  done(); await task;
  lockForUpdate(true);
  let started = false;
  const waiting = updateTask(async () => { started = true; return 1; });
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(started).toBe(false);
  expect(updateBlockers()).toEqual([]);
  lockForUpdate(false);
  expect(await waiting).toBe(1);
});

// 场景：页面确认与检查更新争用操作锁而失败一次，必须重试成功，不能留给看门狗回退正常的新版界面。
test('页面确认失败后重试，成功后才自动检查', async () => {
  const calls = desktop({ readyFailures: 1 }); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(calls.mock.calls.filter(([, args]) => args.operation === 'check')).toHaveLength(1), { timeout: 4000 });
  expect(calls.mock.calls.filter(([, args]) => args.operation === 'ready')).toHaveLength(2);
}, 10000);

// 场景：更新存储不可用（目录只读、文件锁不支持或另一实例正在安装）时客户端照常启动，只说明无法更新。
test('更新存储不可用时显示原因并禁用检查', async () => {
  const notice = '客户端更新暂不可用：另一个客户端正在安装更新；当前版本可正常使用，重启客户端后重试';
  const calls = desktop({ status: { enabled: false, notice } }); await initializeUpdates();
  render(<ClientUpdate />);
  expect(screen.getByRole('status').textContent).toBe(notice);
  expect((screen.getByRole('button', { name: '检查更新' }) as HTMLButtonElement).disabled).toBe(true);
  expect(calls.mock.calls.some(([, args]) => args.operation === 'check')).toBe(false);
});

// 场景：字效工作区已挂载并同步后台会话（跳过 SSE 打开的会话），等待更新时不再重复拉取；工作区未挂载时才由更新流程同步。
test('工作区挂载时更新等待不重复同步后台会话', async () => {
  const storage = memoryStorage();
  let reads = 0;
  setStorage({ ...storage, works: async (server) => { reads++; return storage.works(server); } });
  desktop(); await initializeUpdates(); await checkUpdate();
  // 保留草稿让更新停在等待阶段。
  renderHook(() => useUpdateBlocker('模板尚未保存', true));
  const workspace = renderHook(() => useBackgroundSync(() => null, () => {}));
  await waitFor(() => expect(reads).toBe(1));
  await act(async () => { await downloadUpdate(); });
  expect(reads).toBe(1);
  cancelUpdate(); workspace.unmount();
  await act(async () => { await downloadUpdate(); });
  await waitFor(() => expect(reads).toBe(2));
});

// 场景：deb 只引导下载，展示已签名清单的摘要供安装前核对，不调用自动安装。
test('deb 引导下载并给出摘要，不自动安装', async () => {
  const calls = desktop({ mode: 'manual' }); await initializeUpdates();
  render(<ClientUpdate />);
  await waitFor(() => expect(screen.getByRole('button', { name: '获取安装包' })).toBeTruthy());
  await act(async () => { await downloadUpdate(); });
  expect(screen.getByText('a'.repeat(64))).toBeTruthy();
  expect(screen.getByRole('link').getAttribute('href')).toBe('https://github.com/example/release.deb');
  expect(calls.mock.calls.some(([, args]) => args.operation === 'apply')).toBe(false);
});

// 场景：读取后台状态期间取消，迟到的任务回执不能覆盖取消说明或触发应用。
test('取消后忽略迟到的后台任务回执', async () => {
  let resolve!: (value: Pending) => void;
  const calls = desktop({ pending: new Promise<Pending>(done => { resolve = done; }) });
  await initializeUpdates(); render(<ClientUpdate />);
  await waitFor(() => expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy());
  await act(async () => { await downloadUpdate(); });
  await act(async () => { cancelUpdate(); resolve(OTHER_SERVER); });
  expect(screen.getByText('已取消此次应用，当前版本保持不变')).toBeTruthy();
  expect(calls.mock.calls.some(([, args]) => args.operation === 'apply')).toBe(false);
});

// 场景：启动后的自动检查发现新版本时弹出提醒；稍后关闭后，本次运行中手动检查不再反复弹窗。
test('启动检查发现新版本时弹窗提醒，稍后不再重复打扰', async () => {
  const calls = desktop(); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('发现新版本 v1.2.4'));
  expect(screen.getByRole('dialog').textContent).toContain('一键更新');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '稍后提醒' })); });
  expect(screen.queryByRole('dialog')).toBeNull();
  await act(async () => { await checkUpdate(); });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(calls.mock.calls.filter(([, args]) => args.operation === 'check').length).toBeGreaterThanOrEqual(2);
});

// 场景：提醒弹窗中的一键更新复用设置里的下载流程，用户不需要先打开设置。
test('提醒弹窗可以直接下载更新', async () => {
  const calls = desktop(); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '一键更新' })); });
  await waitFor(() => expect(calls.mock.calls.filter(([, args]) => args.operation === 'download')).toHaveLength(1));
});

// 场景：启动提醒还在显示时打开设置并触发手动检查，提醒不能被静默关闭。
test('打开设置检查更新时保留启动提醒', async () => {
  const calls = desktop(); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('发现新版本 v1.2.4'));
  await act(async () => { render(<ClientUpdate />); });
  await waitFor(() => expect(calls.mock.calls.filter(([, args]) => args.operation === 'check')).toHaveLength(2));
  await waitFor(() => expect(screen.getByRole('dialog').textContent).toContain('发现新版本 v1.2.4'));
  expect(screen.getByRole('dialog').textContent).toContain('一键更新');
});

// 场景：下载失败时提醒弹窗保留错误，用户可以直接重试而不必重新打开设置。
test('提醒弹窗下载失败后允许重试', async () => {
  const calls = desktop({ downloadFail: true }); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '一键更新' })); });
  await waitFor(() => expect(screen.getByRole('button', { name: '重试更新' })).toBeTruthy());
  expect(screen.getByRole('dialog').textContent).toContain('下载失败');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试更新' })); });
  await waitFor(() => expect(calls.mock.calls.filter(([, args]) => args.operation === 'download')).toHaveLength(2));
});

// 场景：deb 提醒在弹窗内展示签名摘要和下载源，不必先进入设置。
test('提醒弹窗展示 deb 下载源与签名摘要', async () => {
  desktop({ mode: 'manual' }); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('button', { name: '获取安装包' })).toBeTruthy());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '获取安装包' })); });
  await waitFor(() => expect(screen.getByRole('link').getAttribute('href')).toBe('https://github.com/example/release.deb'));
  const dialog = screen.getByRole('dialog');
  expect(dialog.textContent).toContain('下载源 1');
  expect(dialog.textContent).toContain('a'.repeat(64));
});

// 场景：下载进行中弹窗不能关闭，也不能再次触发更新。
test('下载期间提醒弹窗锁定关闭', async () => {
  let finish!: () => void;
  const downloadGate = new Promise<void>(resolve => { finish = resolve; });
  const calls = desktop({ downloadGate }); await initializeUpdates();
  render(<UpdateLifecycle />);
  await waitFor(() => expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '一键更新' })); });
  await waitFor(() => expect(screen.getByRole('button', { name: '正在下载…' })).toBeTruthy());
  expect(screen.queryByRole('button', { name: '稍后提醒' })).toBeNull();
  expect(screen.queryByText('关闭')).toBeNull();
  expect(screen.getByRole('dialog')).toBeTruthy();
  expect(calls.mock.calls.filter(([, args]) => args.operation === 'download')).toHaveLength(1);
  await act(async () => { finish(); });
});
