/** 任务消息下的循环记录：按轮列出宿主实际执行的工具调用与结论，可折叠并能逐轮展开。 */
import { useEffect, useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LoopRound, RoundCall, SessionJob } from "./model";

/** 循环层级只用宿主上报的角色名，不展示计划目标或任何模型原文。 */
const layerLabels: Record<LoopRound["layer"], string> = {
  outer: "任务循环",
  plan: "计划循环",
  executor: "执行循环",
};

/** 一项调用的结论文案；失败时保留宿主的错误码与截断后的简短说明。 */
function callText(call: RoundCall): string {
  if (call.status === "pass") return `${call.tool} → 成功`;
  const detail = [call.error_code, call.message].filter(Boolean).join("：");
  return `${call.tool} → 失败${detail ? ` · ${detail}` : ""}`;
}

/** 运行中默认展开循环记录，终态收起；没有记录的旧任务不渲染任何内容。 */
export function LoopRounds({ job }: { job: SessionJob }) {
  const rounds = job.rounds ?? [];
  const active = ["queued", "running"].includes(job.status);
  const [expanded, setExpanded] = useState(active);
  const [open, setOpen] = useState<string | null>(null);
  const listId = useId();
  useEffect(() => setExpanded(active), [active, job.id]);
  if (!rounds.length) return null;
  return (
    <section
      aria-label="循环记录"
      className="rounded-xl border bg-muted/30 p-3 text-xs"
    >
      <Button
        variant="ghost"
        className="h-auto w-full justify-between whitespace-normal px-1 py-1 text-left"
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="text-sm">循环记录 · {rounds.length} 轮</span>
        <ChevronDown
          aria-hidden
          className={cn(
            "size-4 shrink-0 transition-transform",
            expanded && "rotate-180",
          )}
        />
      </Button>
      <ol id={listId} hidden={!expanded} className="mt-3 space-y-2 border-l pl-3">
        {rounds.map((round) => {
          const key = `${round.layer}-${round.turn}`;
          const opened = open === key;
          return (
            <li key={key}>
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 whitespace-normal px-1 py-1 text-left font-normal"
                aria-expanded={opened}
                onClick={() => setOpen(opened ? null : key)}
              >
                <ChevronDown
                  aria-hidden
                  className={cn(
                    "size-3.5 shrink-0 transition-transform",
                    opened && "rotate-180",
                  )}
                />
                <span>
                  {layerLabels[round.layer]} 第 {round.turn} 轮
                </span>
                <span className="ml-auto text-muted-foreground">
                  {round.calls.length
                    ? `${round.calls.length} 次调用`
                    : "未调用工具"}
                </span>
              </Button>
              {opened && (
                <ul className="mt-1 space-y-1 pl-6 text-muted-foreground">
                  {round.calls.length ? (
                    round.calls.map((call, index) => (
                      <li key={`${call.tool}-${index}`} className="break-words">
                        {callText(call)}
                      </li>
                    ))
                  ) : (
                    <li>本轮只返回了消息，没有调用工具。</li>
                  )}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
