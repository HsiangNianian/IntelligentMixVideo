/** 循环位置展示回归：层级与计数渲染、无计划不编造步骤、终态不展示；HTTP 使用替身。 */
import { expect, test } from "bun:test";
import { render, screen } from "@testing-library/react";
import { TaskStatus } from "@/features/remotion_templates/TaskStatus";
import type {
  LoopPosition,
  ProgressStep,
  SessionJob,
} from "@/features/remotion_templates/model";
import { remotionJob } from "./remotion-fixtures";

/** 固定服务端时间与阶段，避免依赖当前时间或本机服务。 */
const STEPS: ProgressStep[] = [
  {
    phase: "generating",
    started_at: "2026-09-14T08:00:00Z",
    ended_at: null,
    status: "active",
  },
];

/** 构造带循环位置的公开任务；位置字段与服务端 LoopPosition 契约一致。 */
function loopJob(
  status: SessionJob["status"] = "running",
  loop: LoopPosition | null = { layer: "plan", turn: 2, step_index: 1, step_total: 3 },
  progress: ProgressStep[] = STEPS,
): SessionJob {
  return {
    ...remotionJob(status),
    created_at: "2026-09-14T08:00:00Z",
    updated_at: "2026-09-14T08:01:08Z",
    parameters: null,
    progress,
    loop,
  };
}

// 运行中的任务展示当前所在的循环层级、轮次与已进入的步骤位置。
test("运行中的任务展示当前循环层级与计数", () => {
  render(<TaskStatus job={loopJob()} />);
  expect(screen.getByText("计划循环 第 2 轮 · 步骤 1/3")).toBeTruthy();
});

// 每个层级使用固定中文文案，不展示模型角色以外的措辞。
test.each([
  ["outer", "任务循环 第 1 轮"],
  ["plan", "计划循环 第 1 轮"],
  ["executor", "执行循环 第 1 轮"],
] as const)("层级 %s 使用固定文案", (layer, expected) => {
  render(<TaskStatus job={loopJob("running", { layer, turn: 1 })} />);
  expect(screen.getByText(expected)).toBeTruthy();
});

// 宿主没有上报步骤时不编造步骤数，也不显示百分比。
test("没有计划时不编造步骤数", () => {
  render(<TaskStatus job={loopJob("running", { layer: "outer", turn: 4 })} />);
  const text = screen.getByText(/任务循环 第 4 轮/);
  expect(text.textContent).toBe("任务循环 第 4 轮");
  expect(text.textContent).not.toContain("步骤");
  expect(text.textContent).not.toContain("%");
});

// 循环位置是当前快照：任务结束后不再展示，避免把旧位置当成进行中。
test.each(["succeeded", "failed", "cancelled"] as const)(
  "终态 %s 不展示循环位置",
  (status) => {
    render(<TaskStatus job={loopJob(status)} />);
    expect(screen.queryByText(/循环 第/)).toBeNull();
  },
);

// 没有阶段记录时仍展示循环位置，循环可见性不依赖阶段时间线。
test("没有阶段记录时仍展示循环位置", () => {
  render(
    <TaskStatus
      job={loopJob("running", { layer: "executor", turn: 7, step_index: 2, step_total: 5 }, [])}
    />,
  );
  expect(screen.getByText(/执行循环 第 7 轮 · 步骤 2\/5/)).toBeTruthy();
});

// 历史任务没有位置字段时保持原样，不补造循环信息。
test("缺少位置字段的历史任务不补造循环", () => {
  render(<TaskStatus job={loopJob("running", null)} />);
  expect(screen.queryByText(/循环 第/)).toBeNull();
  expect(screen.getByText(/已用/)).toBeTruthy();
});

// 组件只渲染契约内的字段，任何模型原文都不会出现在循环文案里。
test("循环文案只包含层级与计数", () => {
  render(<TaskStatus job={loopJob()} />);
  const text = screen.getByText(/计划循环 第 2 轮/).textContent ?? "";
  expect(text).toBe("计划循环 第 2 轮 · 步骤 1/3");
});
