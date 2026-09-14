/** 根据服务端任务时间恢复耗时；只在当前任务运行时刷新时钟，卸载后清理。 */
import { useEffect, useState } from "react";
import { jobLabel, type SessionJob } from "./model";

/** 运行任务以当前时间计时，终态使用服务端更新时间，切换或刷新不丢失起点。 */
export function TaskStatus({ job }: { job: SessionJob }) {
  const [now, setNow] = useState(Date.now);
  const active = ["queued", "running"].includes(job.status);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, job.id]);
  const seconds = Math.max(
    0,
    Math.round(
      ((active ? now : Date.parse(job.updated_at)) -
        Date.parse(job.created_at)) /
        1000,
    ),
  );
  return (
    <div
      title={`任务 ${job.id}`}
      className="border-b px-5 py-2 text-xs leading-5 text-muted-foreground"
    >
      {jobLabel(job.status)} · {active ? "已用" : "耗时"} {seconds} 秒
      <time dateTime={job.created_at} className="ml-2">
        {new Date(job.created_at).toLocaleString("zh-CN")}
      </time>
    </div>
  );
}
