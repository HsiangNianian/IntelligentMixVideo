/** 客户端更新流程测试：隔离 IPC 和网络，验证草稿、后台任务、重复点击及等待取消；不代替真实桌面安装测试。 */
import { expect, mock, test } from 'bun:test';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { mockDesktop } from './setup';
import { ClientUpdate, UpdateLifecycle } from '@/features/updates/ClientUpdate';
import { checkUpdate, downloadUpdate, initializeUpdates, cancelUpdate, tryApplyUpdate } from '@/features/updates/store';
import { useUpdateBlocker, updateTask, updateBlockers, lockForUpdate } from '@/lib/update-guard';
import { recovery } from '@/features/updates/recovery';

function desktop({ pending = 0, fail = false, mode = 'hot' }: { pending?: number | Promise<number>; fail?: boolean; mode?: string } = {}) {
  const calls = mock(async (command: string, args: Record<string, unknown>) => {
    if (command === 'plugin:event|listen') return 1;
    if (command === 'remotion_sessions') return pending;
    if (command !== 'client_update') return null;
    if (args.operation === 'status') return { enabled: true, nativeVersion: '1.2.0', notice: '', resume: null };
    if (args.operation === 'check') {
      if (fail) throw new Error('签名验证失败');
      return { version: '1.2.4', mode };
    }
    if (args.operation === 'download' && mode === 'manual') return { links: ['https://github.com/example/release.deb'], sha256: 'a'.repeat(64) };
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
// 场景：当前页没有任务，但本机后台记录仍在运行；取消等待不会安装或取消业务任务。
test('后台任务阻止应用，取消等待不取消任务', async () => {
  const calls = desktop({ pending: 1 });
  await initializeUpdates(); await checkUpdate();
  await downloadUpdate(); await tryApplyUpdate();
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
// 场景：正在上传时阻止刷新；进入应用阶段后拒绝新的业务请求。
test('进行中请求参与门禁，锁定期间不能发起新请求', async () => {
  let done!: () => void;
  const task = updateTask(() => new Promise<void>(resolve => { done = resolve; }));
  expect(updateBlockers()).toContain('正在完成保存、上传或读取请求');
  done(); await task;
  lockForUpdate(true);
  await expect(updateTask(async () => 1)).rejects.toThrow('正在应用');
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
  let resolve!: (value: number) => void;
  const calls = desktop({ pending: new Promise<number>(done => { resolve = done; }) });
  await initializeUpdates(); render(<ClientUpdate />);
  await waitFor(() => expect(screen.getByRole('button', { name: '一键更新' })).toBeTruthy());
  await act(async () => { await downloadUpdate(); });
  await act(async () => { cancelUpdate(); resolve(1); });
  expect(screen.getByText('已取消此次应用，当前版本保持不变')).toBeTruthy();
  expect(calls.mock.calls.some(([, args]) => args.operation === 'apply')).toBe(false);
});
