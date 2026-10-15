/**
 * 任务消息下的循环记录：按轮列出宿主实际执行的工具调用，可折叠并能逐轮展开。
 * 每个调用行只显示工具名与模型给出的参数（单行摘要），点开看原始 tool call
 * （名称与参数 JSON，失败时附结论与真实错误），用于在聊天里核对模型实际发了什么。
 */
import { useEffect, useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LoopRound, RoundCall, SessionJob } from "./model";

/** 循环层级只用宿主上报的角色名。 */
const layerLabels: Record<LoopRound["layer"], string> = {
  outer: "任务循环",
  plan: "计划循环",
  executor: "执行循环",
};

/** 解析模型发送的参数文本；服务端截断过或模型发了非法 JSON 时返回 undefined。 */
function parseArguments(call: RoundCall): unknown {
  try {
    return call.arguments ? JSON.parse(call.arguments) : undefined;
  } catch {
    return undefined;
  }
}

/** 参数的单行摘要：对象写成 key=value，值过长截断；无法解析时直接截取原文。 */
function argumentSummary(call: RoundCall): string {
  const parsed = parseArguments(call);
  const text =
    parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? Object.entries(parsed)
          .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
          .join("  ")
      : (call.arguments ?? "");
  return text.replace(/\s+/g, " ");
}

/** 原始 tool call：模型请求的名称加参数（可解析时缩进，否则原文），失败时附结论。 */
function rawCall(call: RoundCall): string {
  const parsed = parseArguments(call);
  const raw = {
    name: call.requested_tool ?? call.tool,
    arguments: parsed ?? call.arguments ?? null,
    ...(call.status === "fail"
      ? { error: [call.error_code, call.message, call.detail].filter(Boolean).join("：") }
      : {}),
  };
  return JSON.stringify(raw, null, 2);
}

/** 一次调用：名称与参数摘要在一行，点开显示原始 tool call。 */
function CallRow({ call }: { call: RoundCall }) {
  const [open, setOpen] = useState(false);
  const failed = call.status === "fail";
  return (
    <li>
      <Button
        variant="ghost"
        className="h-auto w-full items-start justify-start gap-2 whitespace-normal px-1 py-1 text-left font-normal"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronDown
          aria-hidden
          className={cn("mt-0.5 size-3.5 shrink-0 transition-transform", open && "rotate-180")}
        />
        <span className="min-w-0 flex-1">
          <span className={cn("font-mono", failed ? "text-destructive" : "text-foreground")}>
            {call.requested_tool ?? call.tool}
            {failed && ` ✗ ${call.error_code ?? ""}`}
          </span>
          <span className="mt-0.5 block truncate font-mono text-muted-foreground">
            {argumentSummary(call) || "无参数"}
          </span>
        </span>
      </Button>
      {open && (
        <pre className="ml-6 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background/60 p-2 font-mono text-[11px] leading-relaxed text-foreground">
          {rawCall(call)}
        </pre>
      )}
    </li>
  );
}

/** 没有调用时说明本轮为什么没有动作，并附模型回复或失败原因（有的话）。 */
function EmptyRound({ round }: { round: LoopRound }) {
  const reason =
    round.error_code === "MODEL_CONTRACT_FAILED"
      ? "模型响应格式无效，本轮未调用工具。"
      : round.error_code === "MODEL_FAILED"
        ? "模型请求失败，本轮未调用工具。"
        : round.error_code === "CANCELLED"
          ? "本轮已取消。"
          : round.message ? "本轮没有调用工具，模型回复："
          : "本轮只返回了消息，没有调用工具。";
  const text = round.error_detail ?? round.message;
  return (
    <div className="space-y-1 pl-6 text-muted-foreground">
      <p>{reason}</p>
      {text && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background/60 p-2 font-mono text-[11px] leading-relaxed text-foreground">
          {text}
        </pre>
      )}
    </div>
  );
}

/** 运行中默认展开循环记录和最新一轮，终态收起；没有记录的旧任务不渲染任何内容。 */
export function LoopRounds({ job }: { job: SessionJob }) {
  const rounds = job.rounds ?? [];
  const active = ["queued", "running"].includes(job.status);
  const [expanded, setExpanded] = useState(active);
  // 用户手动展开或收起过的轮；没有手动操作时，运行中的最新一轮自动展开。
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const listId = useId();
  useEffect(() => {
    setExpanded(active);
    setToggled({});
  }, [active, job.id]);
  if (!rounds.length) return null;
  const latest = rounds[rounds.length - 1]!;
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
          const opened = toggled[key] ?? (active && round === latest);
          const failed = !!round.error_code || round.calls.some((call) => call.status === "fail");
          return (
            <li key={key}>
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 whitespace-normal px-1 py-1 text-left font-normal"
                aria-expanded={opened}
                onClick={() => setToggled((value) => ({ ...value, [key]: !opened }))}
              >
                <ChevronDown
                  aria-hidden
                  className={cn(
                    "size-3.5 shrink-0 transition-transform",
                    opened && "rotate-180",
                  )}
                />
                <span className={cn("font-medium", failed && "text-destructive")}>
                  {layerLabels[round.layer]} 第 {round.turn} 轮
                </span>
                <span className="ml-auto text-muted-foreground">
                  {round.calls.length
                    ? `${round.calls.length} 次调用`
                    : round.error_code ? "本轮未完成" : "未调用工具"}
                </span>
              </Button>
              {opened && (
                round.calls.length ? (
                  <ul className="mt-1 space-y-1 pl-6">
                    {round.calls.map((call, index) => (
                      <CallRow key={`${call.tool}-${index}`} call={call} />
                    ))}
                  </ul>
                ) : (
                  <EmptyRound round={round} />
                )
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
