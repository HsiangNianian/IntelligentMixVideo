/** 操练场行为测试：隔离 HTTP 与桌面存储，覆盖模板选择、提交查询、成片恢复和查询失败；执行 bun run test playground.test.tsx。 */
import { expect, jest, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PlaygroundWorkspace } from "@/features/video_composition/PlaygroundWorkspace";
import { emptyPlayground, loadPlayground, savePlayground } from "@/features/video_composition/playgroundState";
import { apiBase, setApiBase } from "@/lib/api-base";
import type { CompositionTask } from "@/features/video_composition/api";
import { fetchMock, mockDesktop } from "./setup";
import { jsonListResponse, savedTemplate } from "./fixtures";

/** 成功结果使用独立视频地址，模拟真实查询响应。 */
function completed(taskId = "task-1"): CompositionTask {
  return { taskId, status: "succeeded", stage: "completed", result: { videoUrl: "https://media.test/playground.mp4", durationSeconds: 8 }, error: null, createdAt: "2026-10-26T00:00:00Z", updatedAt: "2026-10-26T00:01:00Z" };
}

/** 通过真实下拉选择模板，确保用户能看到 UUID 与名称。 */
async function selectTemplate() {
  const template = savedTemplate();
  const control = await screen.findByRole<HTMLButtonElement>("combobox", { name: "styleId（模板）" });
  await waitFor(() => expect(control.disabled).toBe(false));
  fireEvent.keyDown(control, { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: `${template.template_id}（${template.name}）` }));
}

// 场景：模板下拉提交唯一请求、原 GET 取得成片，重新挂载恢复选择与视频，不再次合成。
test("模板选择后只提交一次，完成后重开恢复视频", async () => {
  const requests: { url: string; body?: string | null }[] = [];
  fetchMock.mockImplementation((async (url, init) => {
    requests.push({ url: String(url), body: init?.body as string });
    if (String(url).endsWith("/template")) return jsonListResponse([savedTemplate()]);
    if (String(url).endsWith("/playground")) return Response.json({ code: 200, data: "task-1" });
    return Response.json(completed());
  }) as typeof fetch);
  const view = render(<PlaygroundWorkspace />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: /情景展示/ }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: /新闻体/ }).disabled).toBe(true);
  await selectTemplate();
  const submit = screen.getByRole("button", { name: "开始合成" });
  fireEvent.click(submit);
  fireEvent.click(submit);
  const player = await screen.findByLabelText<HTMLVideoElement>("合成视频播放器");
  expect(player.src).toBe(completed().result!.videoUrl);
  await waitFor(async () => expect((await loadPlayground(apiBase())).state.video?.taskId).toBe("task-1"));
  expect(requests.filter((item) => item.body).map((item) => JSON.parse(item.body!))).toEqual([{ mode: "standard", styleId: savedTemplate().template_id }]);
  view.unmount();
  render(<PlaygroundWorkspace />);
  expect((await screen.findByLabelText<HTMLVideoElement>("合成视频播放器")).src).toBe(player.src);
  expect((await screen.findByRole("combobox", { name: "styleId（模板）" })).textContent).toContain(savedTemplate().name);
  expect(requests.filter((item) => item.body)).toHaveLength(1);
});

// 场景：查询断线不把任务改为失败，继续查询不重发 POST；任务失败仍保留上一条成片。
test("查询失败可继续查询，任务失败保留最近成片", async () => {
  const previous = completed("old-task");
  const { key } = await loadPlayground(apiBase());
  await savePlayground(key, { ...emptyPlayground(), styleId: savedTemplate().template_id,
    taskStyleId: savedTemplate().template_id, taskTemplateName: savedTemplate().name,
    task: { ...previous, taskId: "pending", status: "processing", stage: "rendering", result: null },
    video: { ...previous.result!, taskId: previous.taskId, styleId: savedTemplate().template_id, templateName: "之前的模板" } });
  let reads = 0;
  fetchMock.mockImplementation((async (url, init) => {
    expect(init?.method).not.toBe("POST");
    if (String(url).endsWith("/template")) return jsonListResponse([savedTemplate()]);
    if (++reads === 1) throw new Error("测试网络断开");
    return Response.json({ ...previous, taskId: "pending", status: "failed", stage: "failed", result: null, error: { code: "ims_failed", stage: "rendering", message: "测试渲染失败" } });
  }) as typeof fetch);
  render(<PlaygroundWorkspace />);
  await screen.findByText(/测试网络断开/);
  expect((await loadPlayground(apiBase())).state.task?.status).toBe("processing");
  fireEvent.click(screen.getByRole("button", { name: "查询进度" }));
  await screen.findByText("测试渲染失败");
  expect(screen.getByLabelText<HTMLVideoElement>("合成视频播放器").src).toBe(previous.result!.videoUrl);
  expect(reads).toBe(2);
  expect(screen.getByRole("progressbar").getAttribute("aria-valuetext")).toBe("失败于正在 IMS 渲染");
});

// 场景：恢复进行中任务时显示阶段；状态推进不会额外查询，15 秒轮询与手动查询串行，成功后停止轮询。
test("展示 IMS 渲染进度，15 秒自动查询并支持手动刷新", async () => {
  const task: CompositionTask = { ...completed(), status: "queued", stage: "queued", result: null };
  const { key } = await loadPlayground(apiBase());
  await savePlayground(key, { ...emptyPlayground(), task });
  const pending = Promise.withResolvers<Response>();
  let reads = 0;
  fetchMock.mockImplementation((async (url, init) => {
    expect(init?.method).not.toBe("POST");
    if (String(url).endsWith("/template")) return jsonListResponse([savedTemplate()]);
    expect(String(url)).toEndWith(`/video-compositions/${task.taskId}`);
    reads += 1;
    return reads < 3 ? Response.json({ ...task, status: "processing", stage: reads === 1 ? "rendering" : "uploading" }) : reads === 3 ? pending.promise : Response.json(completed());
  }) as typeof fetch);
  jest.useFakeTimers();
  const view = render(<PlaygroundWorkspace />);
  try {
    await act(async () => {});
    expect(reads).toBe(1);
    expect(screen.getByRole("status").textContent).toBe("正在 IMS 渲染");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("4");
    await act(async () => { jest.advanceTimersByTime(14_999); });
    expect(reads).toBe(1);
    await act(async () => { jest.advanceTimersByTime(1); });
    expect(reads).toBe(2);
    expect(screen.getByRole("status").textContent).toBe("正在获取成片并转存 ZOS");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("5");
    fireEvent.click(screen.getByRole("button", { name: "查询进度" }));
    await act(async () => {});
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "查询中…" }).disabled).toBe(true);
    await act(async () => { jest.advanceTimersByTime(30_000); });
    expect(reads).toBe(3);
    await act(async () => { pending.resolve(Response.json(completed())); });
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("6");
    expect(screen.getByLabelText<HTMLVideoElement>("合成视频播放器").src).toBe(completed().result!.videoUrl);
    await act(async () => { jest.advanceTimersByTime(30_000); });
    expect(reads).toBe(3);
    fireEvent.click(screen.getByRole("button", { name: "查询进度" }));
    await act(async () => {});
    expect(reads).toBe(4);
  } finally {
    view.unmount();
    jest.useRealTimers();
  }
});

// 场景：服务端样本未初始化时展示 detail，保留所选模板，允许修复配置后重新提交。
test("未初始化提示保留模板选择", async () => {
  fetchMock.mockImplementation((async (url) => String(url).endsWith("/template") ? jsonListResponse([savedTemplate()])
    : Response.json({ detail: "该模式的操练场样本尚未初始化" }, { status: 409 })) as typeof fetch);
  render(<PlaygroundWorkspace />);
  await selectTemplate();
  fireEvent.click(screen.getByRole("button", { name: "开始合成" }));
  await screen.findByText(/该模式的操练场样本尚未初始化/);
  expect(screen.getByRole("combobox", { name: "styleId（模板）" }).textContent).toContain(savedTemplate().name);
  expect((await loadPlayground(apiBase())).state.styleId).toBe(savedTemplate().template_id);
});

// 场景：桌面重启端口变化仍读取同一份原生记录，远程后端用独立记录，保存失败会被调用方看到。
test("桌面记录跨内置端口恢复并隔离远程服务", async () => {
  const settings: Record<string, Record<string, unknown>> = {};
  let fail = false;
  mockDesktop(async (command, args) => {
    expect(command).toBe("local_settings");
    if (args.id) {
      if (fail) throw new Error("磁盘不可写");
      settings[String(args.id)] = args.values as Record<string, unknown>;
    }
    return structuredClone(settings);
  });
  const first = await loadPlayground("http://localhost:10001");
  await savePlayground(first.key, { ...emptyPlayground(), styleId: "saved-style" });
  expect((await loadPlayground("http://localhost:10002")).state.styleId).toBe("saved-style");
  settings.$client = { api_url: "https://remote.test" };
  expect((await loadPlayground("https://remote.test")).state.styleId).toBe("");
  fail = true;
  await expect(savePlayground(first.key, emptyPlayground())).rejects.toThrow("磁盘不可写");
});

// 场景：切换后端不会把旧服务的视频显示为新服务结果。
test("后端切换恢复各自的选择", async () => {
  const original = apiBase();
  fetchMock.mockImplementation((async (_url) => jsonListResponse([savedTemplate()])) as typeof fetch);
  const { key } = await loadPlayground(original);
  await savePlayground(key, { ...emptyPlayground(), styleId: savedTemplate().template_id });
  const view = render(<PlaygroundWorkspace />);
  await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain(savedTemplate().name));
  try {
    setApiBase("https://other-backend.test");
    view.rerender(<PlaygroundWorkspace />);
    await waitFor(() => expect(screen.getByRole("combobox").textContent).toBe("请选择模板"));
  } finally {
    setApiBase(original);
  }
});
