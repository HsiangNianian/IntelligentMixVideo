/** 原生回执文件的 IPC 替身：跨模拟进程和网页存储保留，Rust 用临时真实文件验证相同操作。 */
import type { Job } from "@/features/remotion_templates/model";

/** 每例独立的回执目录；按服务与任务隔离，返回克隆模拟 IPC 序列化。 */
export function registrationJournal() {
  const entries = new Map<string, { server: string; job: Job }>();
  return async ({ operation, payload }: Record<string, unknown>) => {
    const entry = payload as { server: string; job: Job; id: string };
    if (operation === "registrations_list") return structuredClone([...entries.values()]);
    if (operation === "registrations_save") entries.set(JSON.stringify([entry.server, entry.job.id]), structuredClone(entry));
    else if (operation === "registrations_remove") entries.delete(JSON.stringify([entry.server, entry.id]));
    else throw new Error(`未知任务回执操作：${operation}`);
    return null;
  };
}
