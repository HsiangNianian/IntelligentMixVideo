/** 阶段时间线的展示、SSE 重连与历史恢复回归；执行 bun run test，HTTP 和媒体均使用替身。 */
import { expect, spyOn, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ChatPanel } from "@/features/remotion_templates/ChatPanel";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { TaskStatus } from "@/features/remotion_templates/TaskStatus";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import type {
  SessionJob,
  ChatMessage,
} from "@/features/remotion_templates/model";
import * as api from "@/features/remotion_templates/api";
import { remotionJob } from "./remotion-fixtures";
import { remotionServer } from "./remotion-server";
import { fetchMock } from "./setup";

/** 固定服务端时间，不依赖当前日期或本机服务。 */
function progressJob(status: SessionJob["status"] = "running"): SessionJob {
  return {
    ...remotionJob(status),
    created_at: "2026-09-14T08:00:00Z",
    updated_at: "2026-09-14T08:01:08Z",
    parameters: null,
    progress: [
      {
        phase: "understanding",
        started_at: "2026-09-14T08:00:00Z",
        ended_at: "2026-09-14T08:00:10Z",
        status: "done",
      },
      {
        phase: "rendering",
        started_at: "2026-09-14T08:00:10Z",
        ended_at: null,
        status: "active",
      },
    ],
  };
}

// 运行阶段展开、每秒更新时间；结束自动收起但可展开，终态及卸载清理计时器。
test("时间线计时、折叠与资源清理", async () => {
  const clock = spyOn(Date, "now").mockReturnValue(
    Date.parse("2026-09-14T08:00:20Z"),
  );
  const interval = spyOn(window, "setInterval");
  const clear = spyOn(window, "clearInterval");
  const job = progressJob();
  const view = render(<TaskStatus job={job} />);
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("已用 20 秒")).toBeTruthy();
  expect(screen.getByLabelText("进行中")).toBeTruthy();
  clock.mockReturnValue(Date.parse("2026-09-14T08:00:21Z"));
  await act(async () => {
    (interval.mock.calls.at(-1)![0] as () => void)();
  });
  expect(screen.getByText("已用 21 秒")).toBeTruthy();
  const done = {
    ...job,
    status: "succeeded" as const,
    progress: job.progress!.map((step) => ({
      ...step,
      status: "done" as const,
      ended_at: step.ended_at ?? job.updated_at,
    })),
  };
  view.rerender(<TaskStatus job={done} />);
  expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe(
    "false",
  );
  expect(screen.getByText("耗时 68 秒")).toBeTruthy();
  expect(screen.queryByRole("list")).toBeNull();
  fireEvent.click(screen.getByRole("button"));
  expect(screen.getByRole("list").children).toHaveLength(2);
  expect(clear.mock.calls.length).toBeGreaterThan(0);
  view.rerender(<TaskStatus job={job} />);
  const timerId = interval.mock.results.at(-1)!.value;
  view.unmount();
  expect(clear.mock.calls.some((call) => call[0] === timerId)).toBe(true);
});

// 失败和停止保留最后观察到的阶段，不展示原始错误或虚构后续步骤。
for (const status of ["failed", "cancelled", "interrupted"] as const)
  test(`时间线保留终止阶段：${status}`, () => {
    const job = progressJob(status);
    job.progress![1] = {
      ...job.progress![1],
      ended_at: job.updated_at,
      status: "stopped",
    };
    render(<TaskStatus job={job} />);
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.getByLabelText("已停止")).toBeTruthy();
    expect(screen.getByText("渲染并检查效果")).toBeTruthy();
    expect(screen.queryByText("准备可用结果")).toBeNull();
  });

// 纯回答只有理解与回答，不显示生成或渲染；已有旧任务仍显示原状态与耗时。
test("问答短链路与旧任务兼容", () => {
  const job = progressJob("answered");
  job.progress = job.progress!.map((step, index) => ({
    ...step,
    phase: index ? "answering" : "understanding",
    status: "done",
    ended_at: step.ended_at ?? job.updated_at,
  }));
  const view = render(<TaskStatus job={job} />);
  fireEvent.click(screen.getByRole("button"));
  expect(screen.getByText("整理回答")).toBeTruthy();
  expect(screen.queryByText("渲染并检查效果")).toBeNull();
  view.rerender(<TaskStatus job={{ ...job, progress: [] }} />);
  expect(screen.queryByRole("list")).toBeNull();
  expect(screen.getByText(/已回答 · 耗时 68 秒/)).toBeTruthy();
});

// 工作区通过真实 SSE 消费阶段，断线续传不重复；重新挂载恢复进度且不会轮询任务或重发写入。
test("SSE 阶段推进、断线续传与刷新恢复", async () => {
  const fake = remotionServer((path) =>
    path === "/works"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : undefined,
  );
  await api.create("制作测试标题");
  fake.emit("work-1", { type: "job.updated", data: progressJob() });
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  let view = render(<RemotionWorkspace />);
  await screen.findByRole("region", { name: "任务处理进度" });
  await waitFor(() => expect(fake.streams.size).toBe(1));
  const next = progressJob();
  next.progress![1] = {
    ...next.progress![1],
    ended_at: "2026-09-14T08:00:30Z",
    status: "done",
  };
  next.progress!.push({
    phase: "adjusting_layout",
    started_at: "2026-09-14T08:00:30Z",
    ended_at: null,
    status: "active",
  });
  await act(async () => {
    fake.emit("work-1", { type: "job.updated", data: next });
  });
  let region = screen.getByRole("region", { name: "任务处理进度" });
  expect(within(region).getByText(/调整 1 次/)).toBeTruthy();
  expect(within(region).getByRole("list").children).toHaveLength(3);
  const record = fake.records.at(-1)!;
  await act(async () => {
    for (const stream of fake.streams)
      stream.controller.enqueue(
        new TextEncoder().encode(
          `id: ${record.id}\nevent: ${record.type}\ndata: ${JSON.stringify(record)}\n\n`,
        ),
      );
  });
  expect(within(region).getByRole("list").children).toHaveLength(3);
  await act(async () => {
    for (const stream of fake.streams) {
      fake.streams.delete(stream);
      stream.controller.error(new TypeError("offline"));
    }
  });
  fake.advance(remotionJob("failed"));
  await waitFor(() => expect(screen.getByLabelText("已停止")).toBeTruthy(), {
    timeout: 3500,
  });
  view.unmount();
  await waitFor(() => expect(fake.streams.size).toBe(0));
  view = render(<RemotionWorkspace />);
  region = await screen.findByRole("region", { name: "任务处理进度" });
  expect(within(region).getByRole("list").children).toHaveLength(3);
  expect(within(region).getByText(/调整 1 次/)).toBeTruthy();
  expect(
    fetchMock.mock.calls.filter((call) =>
      /\/jobs\/[^/]+$/.test(String(call[0])),
    ),
  ).toHaveLength(0);
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
  view.unmount();
});

// 同一会话每个任务只显示一条链，用户与助手消息不会重复展示同一任务卡片。
test("历史任务各自显示一条处理链", () => {
  const first = { ...progressJob("failed"), id: "old-job" };
  const second = { ...progressJob("answered"), id: "new-job" };
  first.progress![1] = {
    ...first.progress![1],
    status: "stopped",
    ended_at: first.updated_at,
  };
  second.progress = [
    {
      phase: "answering",
      status: "done",
      started_at: second.created_at,
      ended_at: second.updated_at,
    },
  ];
  const messages: ChatMessage[] = [first, second].flatMap((job, index) => [
    {
      id: `${job.id}-user`,
      job_id: job.id,
      sequence: index * 2 + 1,
      role: "user",
      text: "制作",
    },
    {
      id: `${job.id}-assistant`,
      job_id: job.id,
      sequence: index * 2 + 2,
      role: "assistant",
      text: "结果",
    },
  ]);
  render(
    <ChatPanel
      messages={messages}
      jobs={{ [first.id]: first, [second.id]: second }}
      job={second}
      busy={false}
      disabled={false}
      canStop={false}
      first={false}
      onSend={() => {}}
      onStop={() => {}}
    />,
  );
  const cards = screen.getAllByRole("region", { name: "任务处理进度" });
  expect(cards).toHaveLength(2);
  expect(
    within(cards[0]).getByRole("button").getAttribute("aria-expanded"),
  ).toBe("true");
  expect(
    within(cards[1]).getByRole("button").getAttribute("aria-expanded"),
  ).toBe("false");
});

// 加载旧消息带回对应进度，但不能覆盖此时 SSE 已推进的当前任务；切换及新增不携带旧链路。
test("历史分页与会话切换隔离进度", async () => {
  let release: ((response: Response) => void) | undefined;
  const fake = remotionServer((path, _options, url) =>
    path.endsWith("/session") && url?.searchParams.has("before")
      ? new Promise<Response>((resolve) => {
          release = resolve;
        })
      : undefined,
  );
  await api.create("第一会话");
  await api.create("第二会话");
  fake.emit("work-1", { type: "job.updated", data: progressJob() });
  fake.snapshots.get("work-1")!.next_before = 2;
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.select("work-1"));
  await waitFor(() => expect(result.current.connection).toBe("live"));
  act(() => {
    void result.current.older();
  });
  await waitFor(() => expect(release).toBeDefined());
  const newer = progressJob();
  newer.progress!.push({
    phase: "reviewing",
    status: "active",
    started_at: newer.updated_at,
    ended_at: null,
  });
  await act(async () => {
    fake.emit("work-1", { type: "job.updated", data: newer });
  });
  const old = { ...progressJob("failed"), id: "old-job" };
  await act(async () => {
    release!(
      Response.json({
        ...fake.snapshots.get("work-1"),
        messages: [
          {
            id: "old-message",
            job_id: old.id,
            sequence: 0,
            role: "user",
            text: "更早制作",
          },
        ],
        jobs: [old, progressJob()],
        next_before: null,
      }),
    );
  });
  await waitFor(() => expect(result.current.olderLoading).toBe(false));
  expect(result.current.jobs[newer.id].progress).toHaveLength(3);
  expect(result.current.jobs[old.id].progress).toHaveLength(2);
  act(() => result.current.select("work-2"));
  await waitFor(() => expect(result.current.workId).toBe("work-2"));
  expect(result.current.jobs[old.id]).toBeUndefined();
  act(() => result.current.reset());
  expect(result.current.jobs).toEqual({});
});
