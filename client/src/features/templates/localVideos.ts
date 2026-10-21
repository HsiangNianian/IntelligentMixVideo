/**
 * 本地预览视频：用户为视频 1～3 选择的文件只留在本机，不上传服务端。
 * 桌面端经 Tauri `preview_video_*` 命令复制到应用数据目录，首次使用时读回字节生成 blob URL；
 * 浏览器只登记在页面内存，刷新后回到默认地址。登记的 blob URL 在应用存活期间有效，被替换或移除时释放。
 */
import { updateInvoke as invoke } from "@/lib/update-guard";
import { isTauri } from "@tauri-apps/api/core";
import { debugLog, describe } from "@/lib/diagnostics";

/** 单个本地视频上限，与桌面存储一致；字节需完整经过 IPC 并在页面内存生成 blob。 */
export const LOCAL_VIDEO_MAX_BYTES = 512 * 1024 * 1024;

/** 已登记的本地视频：展示用文件名、可直接播放的 blob URL，以及生成该 URL 的数据（回滚时据此写回）。 */
export type LocalVideo = { name: string; url: string; blob: Blob };

/** 弹窗中一个槽位的来源；新选择的文件带 `file`，已登记的本地视频没有。 */
export type VideoSource = { kind: "url"; url: string } | { kind: "file"; name: string; url: string; file?: File };

const slots: (LocalVideo | undefined)[] = [undefined, undefined, undefined];
let restored: Promise<void> | undefined;

/** 当前登记的本地视频快照，弹窗据此区分文件与 URL 来源。 */
export function localVideoSlots(): readonly (LocalVideo | undefined)[] {
  return [...slots];
}

/** 首次调用时读回桌面已保存的视频；读取列表失败抛错且下次调用重试，浏览器直接返回内存登记。 */
export async function localPreviewVideos(): Promise<readonly (LocalVideo | undefined)[]> {
  restored ??= isTauri() ? restore() : Promise.resolve();
  try { await restored; }
  catch (reason) {
    restored = undefined;
    throw new Error(`读取本地预览视频失败：${reason instanceof Error ? reason.message : String(reason)}`);
  }
  return localVideoSlots();
}

/**
 * 依次读取已保存的视频字节并登记为 blob URL；弹窗已登记的新选择不被覆盖。
 * 同一时刻只持有一个完整大小的缓冲区，避免三个接近上限的视频耗尽内存；
 * 单个槽位读取失败时该槽位回退默认地址，不影响其他槽位（加载预览视频时按 URL 来源会删除其本机副本）。
 */
async function restore() {
  const stored = await invoke<({ name: string; type: string } | null)[]>("preview_video_list");
  for (const [index, meta] of stored.entries()) {
    if (!meta || slots[index]) continue;
    try {
      const blob = new Blob([await invoke<ArrayBuffer>("preview_video_read", { slot: index + 1 })], { type: meta.type });
      slots[index] ??= { name: meta.name, blob, url: URL.createObjectURL(blob) };
    } catch (reason) {
      // 该槽位不可读，按默认地址处理；界面不提示，只写调试日志。
      debugLog("本地预览视频", `视频 ${index + 1}（${meta.name}）读取失败，改用默认地址：${describe(reason)}`);
    }
  }
}

/** 把数据写入桌面存储的指定槽位（索引从 0 开始）。 */
async function save(index: number, name: string, blob: Blob) {
  await invoke("preview_video_write", new Uint8Array(await blob.arrayBuffer()), {
    headers: { "x-imv-slot": String(index + 1), "x-imv-name": encodeURIComponent(name), "x-imv-type": blob.type },
  });
}

/**
 * 弹窗读取三个来源成功后保存：新文件写入桌面存储并登记，改用 URL 的槽位删除本地视频。
 * 先等待桌面恢复结束，使晚到的恢复不会把刚删除的本机副本重新登记；桌面端 URL 来源一律删除对应槽位的副本，
 * 包括恢复失败而未登记的槽位。任一槽位失败时已完成的槽位写回原状（登记与本机存储一并回滚），再抛错；
 * 此时新选择的 blob URL 仍未被登记，由弹窗负责释放。全部成功才释放被替换的 blob URL，
 * 失败时当前预览可能仍在使用，暂不释放。
 */
export async function commitLocalVideos(sources: readonly VideoSource[]): Promise<void> {
  await localPreviewVideos().catch(() => undefined);
  const before = [...slots];
  const done: number[] = [];
  for (const [index, source] of sources.entries()) {
    if (source.kind === "file" ? !source.file : !isTauri() && !before[index]) continue;
    try {
      if (source.kind === "file" && source.file) {
        if (isTauri()) await save(index, source.name, source.file);
        slots[index] = { name: source.name, url: source.url, blob: source.file };
      } else {
        if (isTauri()) await invoke("preview_video_remove", { slot: index + 1 });
        slots[index] = undefined;
      }
      done.push(index);
    } catch (reason) {
      await rollback(before, done);
      // Tauri 命令以字符串拒绝，统一包装为带视频编号的错误供弹窗展示。
      throw new Error(`视频 ${index + 1}：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }
  for (const index of done) {
    const previous = before[index];
    if (previous && previous.url !== slots[index]?.url) URL.revokeObjectURL(previous.url);
  }
}

/** 把已完成的槽位恢复为提交前的登记与本机存储；尽力而为，写回失败的槽位保持当前状态。 */
async function rollback(before: readonly (LocalVideo | undefined)[], done: readonly number[]) {
  for (const index of [...done].reverse()) {
    const previous = before[index];
    try {
      if (isTauri() && previous) await save(index, previous.name, previous.blob);
      else if (isTauri()) await invoke("preview_video_remove", { slot: index + 1 });
      slots[index] = previous;
    } catch { /* 无法写回时保持已提交的新状态，登记与本机存储仍一致 */ }
  }
}
