/** 更新门禁的测试隔离回归；执行 bun run test，不连接网络或桌面 IPC。 */
import { expect, test } from 'bun:test';
import { resetUpdates } from '@/features/updates/store';
import { updateTask, updateBlockers } from '@/lib/update-guard';

// 场景：上一用例的模拟请求未结束，销毁更新单例后不能阻塞新用例；迟到回执也不能清除新请求。
test('重置更新状态隔离旧请求及迟到回执', async () => {
  let finishOld!: () => void;
  let finishCurrent!: () => void;
  const old = updateTask(() => new Promise<void>(resolve => { finishOld = resolve; }));
  let current: Promise<void> | undefined;
  try {
    expect(updateBlockers()).toContain('正在完成保存、上传或读取请求');
    resetUpdates();
    expect(updateBlockers()).toEqual([]);
    current = updateTask(() => new Promise<void>(resolve => { finishCurrent = resolve; }));
    finishOld(); await old;
    expect(updateBlockers()).toContain('正在完成保存、上传或读取请求');
    finishCurrent(); await current;
    expect(updateBlockers()).toEqual([]);
  } finally {
    finishOld(); finishCurrent?.();
    await Promise.all([old, current]);
  }
});
