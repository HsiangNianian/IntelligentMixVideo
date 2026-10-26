/** 操练场只持久化当前表单、在途任务和最近成片；按后端隔离，桌面复用原生设置文件。 */
import { isTauri } from "@tauri-apps/api/core";
import { readSettings, saveSettings } from "@/features/settings/api";
import type { CompositionTask } from "./api";

export type PlaygroundState = {
  styleId: string;
  task: CompositionTask | null;
  taskStyleId: string;
  taskTemplateName: string;
  video: { taskId: string; styleId: string; templateName: string; videoUrl: string; durationSeconds: number } | null;
};

/** 每次返回独立空状态，尚未选择的模板不猜默认值。 */
export function emptyPlayground(): PlaygroundState {
  return { styleId: "", task: null, taskStyleId: "", taskTemplateName: "", video: null };
}

/** 内置后端用固定键，避免随机监听端口使重启后的记录失联。 */
export async function loadPlayground(address: string): Promise<{ key: string; state: PlaygroundState }> {
  const settings = await readSettings();
  const scope = isTauri() && !settings.$client?.api_url ? "embedded" : address.replace(/\/+$/, "");
  const key = `$playground:${scope}`;
  const serialized = isTauri() ? settings[key]?.state : localStorage.getItem(key);
  if (!serialized) return { key, state: emptyPlayground() };
  try {
    const saved = JSON.parse(String(serialized)) as PlaygroundState;
    if (typeof saved.styleId !== "string" || !("task" in saved) || !("video" in saved)) throw new Error();
    return { key, state: saved };
  } catch {
    // 保留原始记录；只将已确定的作用域交给页面，供用户主动重置。
    throw Object.assign(new Error("操练场本地记录无法读取"), { key });
  }
}

// 同一页面中的选择与查询可同时保存；按触发顺序落盘，旧请求不能覆盖新选择。
let writes: Promise<void> = Promise.resolve();

/** 浏览器也保留本页状态；失败交给界面，不静默声称下次能恢复。 */
export function savePlayground(key: string, state: PlaygroundState): Promise<void> {
  const serialized = JSON.stringify(state);
  writes = writes.catch(() => {}).then(async () => {
    if (isTauri()) await saveSettings(key, { state: serialized });
    else localStorage.setItem(key, serialized);
  });
  return writes;
}
