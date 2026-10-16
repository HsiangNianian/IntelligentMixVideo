/**
 * 本地预览视频：用户为视频 1～3 选择的文件只留在本机，不上传服务端。
 * 桌面端经 Tauri `preview_video_*` 命令复制到应用数据目录，首次使用时读回字节生成 blob URL；
 * 浏览器只登记在页面内存，刷新后回到默认地址。登记的 blob URL 在应用存活期间有效，被替换或移除时释放。
 */
import { invoke, isTauri } from "@tauri-apps/api/core";

/** 单个本地视频上限，与桌面存储一致；字节需完整经过 IPC 并在页面内存生成 blob。 */
export const LOCAL_VIDEO_MAX_BYTES = 512 * 1024 * 1024;

/** 已登记的本地视频：展示用文件名与可直接播放的 blob URL。 */
export type LocalVideo = { name: string; url: string };

/** 弹窗中一个槽位的来源；新选择的文件带 `file`，已登记的本地视频没有。 */
export type VideoSource = { kind: "url"; url: string } | { kind: "file"; name: string; url: string; file?: File };

const slots: (LocalVideo | undefined)[] = [undefined, undefined, undefined];
let restored: Promise<void> | undefined;

/** 当前登记的本地视频快照，弹窗据此区分文件与 URL 来源。 */
export function localVideoSlots(): readonly (LocalVideo | undefined)[] {
  return [...slots];
}

/** 首次调用时读回桌面已保存的视频；读取失败抛错且下次调用重试，浏览器直接返回内存登记。 */
export async function localPreviewVideos(): Promise<readonly (LocalVideo | undefined)[]> {
  restored ??= isTauri() ? restore() : Promise.resolve();
  try { await restored; }
  catch (reason) {
    restored = undefined;
    throw new Error(`读取本地预览视频失败：${reason instanceof Error ? reason.message : String(reason)}`);
  }
  return localVideoSlots();
}

/** 逐个读取已保存的视频字节并登记为 blob URL；弹窗已登记的新选择不被覆盖。 */
async function restore() {
  const stored = await invoke<({ name: string; type: string } | null)[]>("preview_video_list");
  await Promise.all(stored.map(async (meta, index) => {
    if (!meta) return;
    const bytes = await invoke<ArrayBuffer>("preview_video_read", { slot: index + 1 });
    slots[index] ??= { name: meta.name, url: URL.createObjectURL(new Blob([bytes], { type: meta.type })) };
  }));
}

/**
 * 弹窗读取三个来源成功后保存：新文件写入桌面存储并登记，改用 URL 的槽位删除本地视频。
 * 每个槽位完成后立即更新登记，使内存与磁盘一致；失败抛错，已完成的槽位保留。
 * 全部成功才释放被替换的 blob URL（调用方随后用新来源替换播放器）；失败时当前预览可能仍在使用，暂不释放。
 */
export async function commitLocalVideos(sources: readonly VideoSource[]): Promise<void> {
  const stale: string[] = [];
  for (const [index, source] of sources.entries()) {
    const current = slots[index];
    const { file } = source.kind === "file" ? source : { file: undefined };
    if (source.kind === "url" ? !current : !file) continue;
    try {
      if (isTauri() && file) await invoke("preview_video_write", new Uint8Array(await file.arrayBuffer()), {
        headers: { "x-imv-slot": String(index + 1), "x-imv-name": encodeURIComponent(file.name), "x-imv-type": file.type },
      });
      else if (isTauri()) await invoke("preview_video_remove", { slot: index + 1 });
    } catch (reason) {
      // Tauri 命令以字符串拒绝，统一包装为带视频编号的错误供弹窗展示。
      throw new Error(`视频 ${index + 1}：${reason instanceof Error ? reason.message : String(reason)}`);
    }
    slots[index] = source.kind === "file" ? { name: source.name, url: source.url } : undefined;
    if (current && current.url !== source.url) stale.push(current.url);
  }
  stale.forEach((url) => URL.revokeObjectURL(url));
}
