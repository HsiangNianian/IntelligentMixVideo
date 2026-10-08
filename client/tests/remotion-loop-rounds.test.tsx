/** 循环记录回归：在对话消息下渲染、逐轮展开看调用与结论、终态折叠、顶部不再显示循环位置。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ChatPanel } from "@/features/remotion_templates/ChatPanel";
import { LoopRounds } from "@/features/remotion_templates/LoopRounds";
import { TaskStatus } from "@/features/remotion_templates/TaskStatus";
import type {
  ChatMessage,
  LoopRound,
  ProgressStep,
  SessionJob,
} from "@/features/remotion_templates/model";
import { remotionJob } from "./remotion-fixtures";

/** 固定服务端阶段，避免依赖当前时间或本机服务。 */
const STEPS: ProgressStep[] = [
  {
    phase: "generating",
    started_at: "2026-09-14T08:00:00Z",
    ended_at: null,
    status: "active",
  },
];

/** 四轮真实形态的记录：控制工具、成功业务工具、失败业务工具与只回消息的轮次。 */
const ROUNDS: LoopRound[] = [
  { layer: "outer", turn: 1, calls: [{ tool: "tools.plan_execute", status: "pass" }] },
  { layer: "executor", turn: 2, calls: [{ tool: "preset.create", status: "pass" }] },
  {
    layer: "executor",
    turn: 3,
    calls: [
      {
        tool: "sprite.create",
        status: "fail",
        error_code: "COMPOSITION_FAILED",
        message: "Sprite code, schema or defaults do not match its instance definition.",
      },
    ],
  },
  { layer: "plan", turn: 4, calls: [] },
];

/** 构造带循环记录的公开任务；记录字段与服务端 LoopRound 契约一致。 */
function roundJob(
  status: SessionJob["status"] = "running",
  rounds: LoopRound[] = ROUNDS,
  progress: ProgressStep[] = STEPS,
): SessionJob {
  return {
    ...remotionJob(status),
    created_at: "2026-09-14T08:00:00Z",
    updated_at: "2026-09-14T08:01:08Z",
    parameters: null,
    progress,
    rounds,
  };
}

/** 在真实聊天面板里渲染一条属于该任务的消息，验证记录出现在对话流内而不是顶部。 */
function chatWith(job: SessionJob) {
  const message: ChatMessage = {
    id: "message-1",
    role: "user",
    text: "做一个标题字效",
    job_id: job.id,
    created_at: "2026-09-14T08:00:00Z",
  };
  return render(
    <ChatPanel
      messages={[message]}
      busy
      disabled={false}
      canStop
      first={false}
      onSend={() => {}}
      onStop={() => {}}
      job={job}
      jobs={{ [job.id]: job }}
    />,
  );
}

// 运行中默认展开，逐轮点开后看到该轮实际调用的工具与结论。
test("运行中的任务在对话消息下展开循环记录，并可逐轮查看调用与结论", () => {
  chatWith(roundJob());
  const log = within(screen.getByRole("log", { name: "聊天消息" }));
  expect(
    log.getByRole("button", { name: /循环记录 · 4 轮/ }).getAttribute("aria-expanded"),
  ).toBe("true");
  fireEvent.click(log.getByRole("button", { name: /执行循环 第 2 轮/ }));
  expect(log.getByText("preset.create → 成功")).toBeTruthy();
  fireEvent.click(log.getByRole("button", { name: /执行循环 第 3 轮/ }));
  expect(
    log.getByText(
      /sprite\.create → 失败 · COMPOSITION_FAILED：Sprite code, schema or defaults/,
    ),
  ).toBeTruthy();
});

// 终态收起整块；没有调用工具的轮次明确说明本轮只回了消息，不伪造调用。
test("终态默认折叠，没有调用的轮次说明本轮只返回了消息", () => {
  render(<LoopRounds job={roundJob("succeeded")} />);
  const summary = screen.getByRole("button", { name: /循环记录 · 4 轮/ });
  expect(summary.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(summary);
  fireEvent.click(screen.getByRole("button", { name: /计划循环 第 4 轮/ }));
  expect(screen.getByText("本轮只返回了消息，没有调用工具。")).toBeTruthy();
});

// 旧任务没有记录时不渲染空的循环区域。
test("没有循环记录的任务不渲染循环区域", () => {
  render(<LoopRounds job={roundJob("running", [])} />);
  expect(screen.queryByText(/循环记录/)).toBeNull();
});

// 阶段时间线只讲阶段与耗时，循环位置已移到对话内的记录里。
test("阶段时间线不再展示循环位置", () => {
  render(<TaskStatus job={roundJob("running")} />);
  expect(screen.getAllByText("生成模板代码").length).toBeGreaterThan(0);
  expect(screen.queryByText(/循环/)).toBeNull();
});
