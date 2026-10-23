/**
 * 本地预览视频：用户为视频 1～3 选择的文件只留在本机，不上传服务端。
 * 桌面端经 Tauri `preview_video_*` 命令复制到应用数据目录，播放时用预览媒体服务按数据文件名提供的 HTTP 地址，
 * 视频字节不经 IPC 读回页面；浏览器只登记在页面内存的 blob URL，刷新后回到默认地址，被替换或移除时释放。
 * 保存的大小与用时、恢复时不可读的槽位写进调试日志：接近上限的视频保存时要完整经过 IPC，变慢时据此判断。
 */
import { updateInvoke as invoke } from "@/lib/update-guard";
import { isTauri } from "@tauri-apps/api/core";
import { briefly, debugLog } from "@/lib/diagnostics";
import { localMediaUrl, mediaServerBase } from "./mediaServer";

/** 单个本地视频上限，与桌面存储一致；保存时字节需完整经过 IPC。 */
export const LOCAL_VIDEO_MAX_BYTES = 512 * 1024 * 1024;

/**
 * 已登记的本地视频：展示用文件名与可直接播放的地址（桌面端为媒体服务地址，浏览器为 blob URL）。
 * `blob` 是回滚时写回的数据：浏览器与本次新选的文件才有，桌面端读回的视频没有，提交替换或删除前再从媒体服务读取。
 */
export type LocalVideo = { name: string; url: string; blob?: Blob };

/** 弹窗中一个槽位的来源；新选择的文件带 `file`，已登记的本地视频没有。 */
export type VideoSource = { kind: "url"; url: string } | { kind: "file"; name: string; url: string; file?: File };

const slots: (LocalVideo | undefined)[] = [undefined, undefined, undefined];
let restored: Promise<void> | undefined;

/** 当前登记的本地视频快照，弹窗据此区分文件与 URL 来源。 */
export function localVideoSlots(): readonly (LocalVideo | undefined)[] {
  return [...slots];
}

/** 保存本地视频的调试记录：槽位、文件名、大小与用时。 */
function logTransfer(action: string, index: number, name: string, bytes: number, started: number) {
  debugLog("本地预览视频", `视频 ${index + 1} ${action} ${name}（${(bytes / 1048576).toFixed(1)} MiB）用时 ${((performance.now() - started) / 1000).toFixed(1)} 秒`, "信息");
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
 * 登记桌面已保存的视频：地址指向媒体服务，不读回字节；弹窗已登记的新选择不被覆盖。
 * 逐个用 HEAD 请求确认数据文件可读，单个槽位不可读时该槽位回退默认地址，不影响其他槽位
 * （加载预览视频时按 URL 来源会删除其本机副本）。
 */
async function restore() {
  const stored = await invoke<({ name: string; type: string; file: string } | null)[]>("preview_video_list");
  const server = await mediaServerBase();
  for (const [index, meta] of stored.entries()) {
    if (!meta || slots[index]) continue;
    const url = localMediaUrl(server, meta.file);
    try {
      const response = await fetch(url, { method: "HEAD" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      slots[index] ??= { name: meta.name, url };
    } catch (reason) {
      // 该槽位不可读，按默认地址处理；界面不提示，只写调试日志。
      debugLog("本地预览视频", `视频 ${index + 1}（${meta.name}）读取失败，改用默认地址：${briefly(reason)}`);
    }
  }
}

/** 把数据写入桌面存储的指定槽位（索引从 0 开始），返回新数据文件在媒体服务上的播放地址。 */
async function save(index: number, name: string, blob: Blob): Promise<string> {
  const started = performance.now();
  // 先确认媒体服务地址，避免写入成功后地址命令失败，留下前端尚未登记的本地文件。
  const server = await mediaServerBase();
  const file = await invoke<string>("preview_video_write", new Uint8Array(await blob.arrayBuffer()), {
    headers: { "x-imv-slot": String(index + 1), "x-imv-name": encodeURIComponent(name), "x-imv-type": blob.type },
  });
  logTransfer("保存", index, name, blob.size, started);
  return localMediaUrl(server, file);
}

/** 从媒体服务读回桌面已保存视频的字节，供提交失败时写回。 */
async function download(url: string): Promise<Blob> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.blob();
}

/**
 * 弹窗读取三个来源成功后保存：新文件写入桌面存储并登记，改用 URL 的槽位删除本地视频。
 * 先等待桌面恢复结束，使晚到的恢复不会把刚删除的本机副本重新登记；桌面端 URL 来源一律删除对应槽位的副本，
 * 包括恢复失败而未登记的槽位。任一槽位失败时已完成的槽位写回原状（登记与本机存储一并回滚），再抛错；
 * 此时新选择的 blob URL 仍未被登记，由弹窗负责释放（桌面端登记的是媒体服务地址，新选择的 blob URL 一律由弹窗释放）。
 * 全部成功才释放被替换的 blob URL，失败时当前预览可能仍在使用，暂不释放。
 */
export async function commitLocalVideos(sources: readonly VideoSource[]): Promise<void> {
  await localPreviewVideos().catch(() => undefined);
  const before = [...slots];
  // 桌面端读回的视频没有字节：替换或删除前先从媒体服务读回，失败时才能写回；读不到就不开始提交。
  for (const [index, source] of sources.entries()) {
    const previous = before[index];
    if (!isTauri() || !previous || previous.blob || (source.kind === "file" && !source.file)) continue;
    try { before[index] = { ...previous, blob: await download(previous.url) }; }
    catch (reason) { throw new Error(`视频 ${index + 1}：原本地视频读取失败，未做修改：${reason instanceof Error ? reason.message : String(reason)}`); }
  }
  const done: number[] = [];
  for (const [index, source] of sources.entries()) {
    if (source.kind === "file" ? !source.file : !isTauri() && !before[index]) continue;
    try {
      if (source.kind === "file" && source.file) {
        // 桌面端改用新数据文件的媒体服务地址播放，读元数据用的 blob URL 由弹窗释放。
        const url = isTauri() ? await save(index, source.name, source.file) : source.url;
        slots[index] = { name: source.name, url, blob: source.file };
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
      // 桌面端写回会生成新的数据文件名，播放地址随之更新；提交前已为这些槽位读回字节。
      if (isTauri() && previous?.blob) slots[index] = { ...previous, url: await save(index, previous.name, previous.blob) };
      else {
        if (isTauri()) await invoke("preview_video_remove", { slot: index + 1 });
        slots[index] = previous;
      }
    } catch { /* 无法写回时保持已提交的新状态，登记与本机存储仍一致 */ }
  }
}
