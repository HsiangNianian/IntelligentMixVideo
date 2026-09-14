/** 根据服务端任务时间恢复耗时；只在当前任务运行时刷新时钟，卸载后清理。 */
import { useEffect, useId, useState } from "react";
import { Check, ChevronDown, Circle, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { jobLabel, type ProgressStep, type SessionJob } from "./model";

/** 公开阶段固定中文文案，绝不展示模型原文或内部错误。 */
const phaseLabels: Record<ProgressStep["phase"], string> = {
  understanding: "理解你的需求",
  target_review: "确认制作目标",
  answering: "整理回答",
  generating: "生成模板代码",
  rendering: "渲染并检查效果",
  reviewing: "验收文字与样式",
  sampling: "补充预览帧",
  adjusting: "调整模板效果",
  adjusting_layout: "调整文字布局",
  adjusting_style: "调整文字样式",
  adjusting_text: "调整文字内容",
  preparing: "准备可用结果",
};

/** 时间完全来自服务端区间，运行阶段才使用本地当前时间。 */
function elapsed(start: string, end: number): number {
  return Math.max(0, Math.round((end - Date.parse(start)) / 1000));
}

/** 运行任务以当前时间计时，终态使用服务端更新时间，切换或刷新不丢失起点。 */
export function TaskStatus({ job }: { job: SessionJob }) {
  const [now, setNow] = useState(Date.now);
  const active = ["queued", "running"].includes(job.status);
  const collapsedOnFinish = ["succeeded", "answered"].includes(job.status);
  const [expanded, setExpanded] = useState(!collapsedOnFinish);
  const contentId = useId();
  useEffect(() => setExpanded(!collapsedOnFinish), [collapsedOnFinish, job.id]);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, job.id]);
  const seconds = elapsed(
    job.created_at,
    active ? now : Date.parse(job.updated_at),
  );
  const steps = job.progress ?? [];
  const adjustments = steps.filter((step) =>
    step.phase.startsWith("adjusting"),
  ).length;
  if (steps.length)
    return (
      <section
        aria-label="任务处理进度"
        className="rounded-xl border bg-muted/30 p-3 text-xs"
      >
        <Button
          variant="ghost"
          className="h-auto w-full justify-between whitespace-normal px-1 py-1 text-left"
          aria-expanded={expanded}
          aria-controls={contentId}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="space-y-1">
            <span className="block text-sm">
              {active ? phaseLabels[steps.at(-1)!.phase] : jobLabel(job.status)}
            </span>
            <span className="block font-normal text-muted-foreground">
              {active ? "已用" : "耗时"} {seconds} 秒
              {adjustments > 0 ? ` · 调整 ${adjustments} 次` : ""}
            </span>
          </span>
          <ChevronDown
            aria-hidden
            className={`size-4 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        </Button>
        <div id={contentId} hidden={!expanded}>
          <ol aria-label="处理阶段" className="mt-3 space-y-3 border-l pl-3">
            {steps.map((step, index) => {
              const running = active && step.status === "active";
              const Icon = running
                ? LoaderCircle
                : step.status === "done"
                  ? Check
                  : Circle;
              return (
                <li
                  key={`${step.started_at}-${index}`}
                  className="flex items-center gap-2"
                >
                  <Icon
                    aria-label={
                      running
                        ? "进行中"
                        : step.status === "done"
                          ? "已结束"
                          : "已停止"
                    }
                    className={`size-3.5 shrink-0 ${running ? "animate-spin text-primary" : "text-muted-foreground"}`}
                  />
                  <span className="flex-1">{phaseLabels[step.phase]}</span>
                  <span className="text-muted-foreground">
                    {elapsed(
                      step.started_at,
                      step.ended_at
                        ? Date.parse(step.ended_at)
                        : active
                          ? now
                          : Date.parse(job.updated_at),
                    )}{" "}
                    秒
                  </span>
                </li>
              );
            })}
          </ol>
          <time
            dateTime={job.created_at}
            className="mt-3 block text-muted-foreground"
          >
            {new Date(job.created_at).toLocaleString("zh-CN")}
          </time>
        </div>
      </section>
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
