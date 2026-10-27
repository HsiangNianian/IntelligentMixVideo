/** 操练场行为测试：隔离 HTTP 与桌面存储，覆盖合成、持久化及查询/本地记录故障恢复；执行 bun run test playground.test.tsx。 */
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

/** 模板列表里已保存模板对应的单选项（名称与 ID 前 8 位组成可读名称）。 */
function templateRadio() {
  return screen.findByRole<HTMLInputElement>("radio", { name: new RegExp(savedTemplate().name) });
}

/** 在模板列表里点选已保存的模板，等列表可用后再点。 */
async function selectTemplate() {
  const radio = await templateRadio();
  await waitFor(() => expect(radio.disabled).toBe(false));
  fireEvent.click(radio);
}

/** 桌面替换设置 IPC；浏览器使用实际 localStorage，允许检查原始损坏记录是否被覆盖。 */
function playgroundStorage(kind: "browser" | "desktop") {
  const settings: Record<string, Record<string, unknown>> = { ims: { region: "keep" } };
  if (kind === "desktop") mockDesktop(async (command, args) => {
    expect(command).toBe("local_settings");
    if (args.id) settings[String(args.id)] = args.values as Record<string, unknown>;
    return structuredClone(settings);
  });
  return {
    settings,
    read: (key: string) => kind === "desktop" ? settings[key]?.state : localStorage.getItem(key),
    write: (key: string, value: string) => {
      if (kind === "desktop") settings[key] = { state: value };
      else localStorage.setItem(key, value);
    },
  };
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
  await waitFor(async () => expect((await templateRadio()).checked).toBe(true));
  expect(requests.filter((item) => item.body)).toHaveLength(1);
});

// 场景：断线与 503 不允许清除任务，继续查询不重发 POST；任务失败仍保留上一条成片。
test.each(["network", "503"])("查询失败可继续查询，任务失败保留最近成片：%s", async (failure) => {
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
    if (++reads === 1) {
      if (failure === "network") throw new Error("测试查询不可用");
      return Response.json({ detail: "测试查询不可用" }, { status: 503 });
    }
    return Response.json({ ...previous, taskId: "pending", status: "failed", stage: "failed", result: null, error: { code: "ims_failed", stage: "rendering", message: "测试渲染失败" } });
  }) as typeof fetch);
  render(<PlaygroundWorkspace />);
  await screen.findByText(/测试查询不可用/);
  expect(screen.queryByRole("button", { name: "清除失效任务" })).toBeNull();
  expect((await loadPlayground(apiBase())).state.task?.status).toBe("processing");
  expect(screen.getByText("上一条成片")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查询进度" }));
  await screen.findByText("测试渲染失败");
  expect(screen.getByLabelText<HTMLVideoElement>("合成视频播放器").src).toBe(previous.result!.videoUrl);
  expect(reads).toBe(2);
  expect(screen.getByRole("progressbar").getAttribute("aria-valuetext")).toBe("失败于正在 IMS 渲染");
  // 新任务失败后没有更新的成片，保留的视频就是最新可用结果。
  expect(screen.getByText("最新成片")).toBeTruthy();
  expect(screen.queryByText("上一条成片")).toBeNull();
});

// 场景：操练场的模板已在受理时固定，template 阶段失败是 Remotion 环境检查，显示在「生成画面与字效」步骤下。
test("模板阶段失败显示在生成画面与字效步骤", async () => {
  const { key } = await loadPlayground(apiBase());
  await savePlayground(key, { ...emptyPlayground(), styleId: savedTemplate().template_id,
    taskStyleId: savedTemplate().template_id, taskTemplateName: savedTemplate().name,
    task: { ...completed("pending"), status: "queued", stage: "queued", result: null } });
  fetchMock.mockImplementation((async (url) => {
    if (String(url).endsWith("/template")) return jsonListResponse([savedTemplate()]);
    return Response.json({ ...completed("pending"), status: "failed", stage: "queued", result: null,
      error: { code: "remotion_render_environment_unavailable", stage: "template", message: "Remotion 渲染环境不可用" } });
  }) as typeof fetch);
  render(<PlaygroundWorkspace />);
  const message = await screen.findByText("Remotion 渲染环境不可用");
  expect(screen.getByRole("progressbar").getAttribute("aria-valuetext")).toBe("失败于生成画面与字效");
  expect(message.parentElement?.textContent).toContain("生成画面与字效");
});

// 场景：旧任务 404 后由用户清除任务，保留选择与成片，重开不再查询旧任务且可以重新合成。
test.each([
  ["browser", "queued"], ["browser", "processing"], ["desktop", "queued"], ["desktop", "processing"],
] as const)("失效任务可清除并重新合成：%s / %s", async (kind, status) => {
  playgroundStorage(kind);
  const template = savedTemplate();
  const { key } = await loadPlayground(apiBase());
  const previous = { taskId: "previous", styleId: template.template_id, templateName: template.name,
    videoUrl: "https://media.test/previous.mp4", durationSeconds: 8 };
  await savePlayground(key, { ...emptyPlayground(), styleId: template.template_id,
    task: { ...completed("missing"), status, stage: status === "queued" ? "queued" : "rendering", result: null },
    taskStyleId: template.template_id, taskTemplateName: template.name, video: previous });
  let missingQueries = 0;
  let posts = 0;
  fetchMock.mockImplementation((async (url, init) => {
    if (String(url).endsWith("/template")) return jsonListResponse([template]);
    if (String(url).endsWith("/missing")) {
      missingQueries += 1;
      return Response.json({ detail: "合成任务不存在" }, { status: 404 });
    }
    if (init?.method === "POST") {
      posts += 1;
      return Response.json({ code: 200, data: "new-task" });
    }
    return Response.json(completed("new-task"));
  }) as typeof fetch);
  const view = render(<PlaygroundWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "清除失效任务" }));
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "开始合成" }).disabled).toBe(false));
  const restored = (await loadPlayground(apiBase())).state;
  expect(restored.task).toBeNull();
  expect(restored.styleId).toBe(template.template_id);
  expect(restored.video).toEqual(previous);
  expect(posts).toBe(0);
  view.unmount();
  render(<PlaygroundWorkspace />);
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "开始合成" }).disabled).toBe(false));
  expect(screen.getByLabelText<HTMLVideoElement>("合成视频播放器").src).toBe(previous.videoUrl);
  expect(missingQueries).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "开始合成" }));
  await waitFor(async () => expect((await loadPlayground(apiBase())).state.video?.taskId).toBe("new-task"));
  expect(posts).toBe(1);
});

// 场景：坏 JSON 或缺字段的记录一直保留到用户主动重置，重试不会删除；重置不影响其他服务和 IMS 设置。
test.each([
  ["browser", "{"], ["browser", '{"styleId":"saved"}'],
  ["desktop", "{"], ["desktop", '{"styleId":"saved"}'],
] as const)("损坏记录可主动重置：%s / %s", async (kind, serialized) => {
  const storage = playgroundStorage(kind);
  const { key } = await loadPlayground(apiBase());
  storage.write(key, serialized);
  storage.write("$playground:other", "untouched");
  fetchMock.mockImplementation((async (url) => {
    expect(String(url)).toEndWith("/template");
    return jsonListResponse([savedTemplate()]);
  }) as typeof fetch);
  const view = render(<PlaygroundWorkspace />);
  await screen.findByText(/无法恢复操练场/);
  expect(storage.read(key)).toBe(serialized);
  expect((await templateRadio()).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
  await screen.findByText(/无法恢复操练场/);
  expect(storage.read(key)).toBe(serialized);
  fireEvent.click(screen.getByRole("button", { name: "重置操练场记录" }));
  await waitFor(async () => expect((await templateRadio()).disabled).toBe(false));
  expect((await loadPlayground(apiBase())).state).toEqual(emptyPlayground());
  expect(storage.read("$playground:other")).toBe("untouched");
  expect(storage.settings.ims).toEqual({ region: "keep" });
  view.unmount();
  render(<PlaygroundWorkspace />);
  await selectTemplate();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "开始合成" }).disabled).toBe(false);
  expect(screen.queryByText(/无法恢复操练场/)).toBeNull();
});

// 场景：重置写入失败时保留原记录与禁用状态，明确报错；存储恢复后可以再次重置。
test("重置写入失败可再次尝试", async () => {
  let serialized = "{";
  let writable = false;
  mockDesktop(async (command, args) => {
    expect(command).toBe("local_settings");
    if (args.id) {
      expect(args.id).toBe("$playground:embedded");
      if (!writable) throw new Error("测试存储不可写");
      serialized = String((args.values as Record<string, unknown>).state);
    }
    return { "$playground:embedded": { state: serialized } };
  });
  fetchMock.mockResolvedValue(jsonListResponse([savedTemplate()]));
  render(<PlaygroundWorkspace />);
  await screen.findByText(/无法恢复操练场/);
  fireEvent.click(screen.getByRole("button", { name: "重置操练场记录" }));
  await screen.findByText(/无法重置操练场：测试存储不可写/);
  expect(serialized).toBe("{");
  expect((await templateRadio()).disabled).toBe(true);
  writable = true;
  fireEvent.click(screen.getByRole("button", { name: "重置操练场记录" }));
  await waitFor(async () => expect((await templateRadio()).disabled).toBe(false));
});

// 场景：设置文件暂时读不到时只提供重试，不猜作用域或重置整个设置；读取恢复后原选择仍在。
test("本地读取失败可重试且不显示重置入口", async () => {
  let reads = 0;
  mockDesktop(async (command, args) => {
    expect(command).toBe("local_settings");
    expect(args.id).toBeUndefined();
    if (++reads === 1) throw new Error("测试读取失败");
    return { "$playground:embedded": { state: JSON.stringify({ ...emptyPlayground(), styleId: savedTemplate().template_id }) } };
  });
  fetchMock.mockResolvedValue(jsonListResponse([savedTemplate()]));
  render(<PlaygroundWorkspace />);
  await screen.findByText(/无法恢复操练场：测试读取失败/);
  expect(screen.queryByRole("button", { name: "重置操练场记录" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
  await waitFor(() => expect(screen.getByRole<HTMLButtonElement>("button", { name: "开始合成" }).disabled).toBe(false));
  await waitFor(async () => expect((await templateRadio()).checked).toBe(true));
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
  expect((await templateRadio()).checked).toBe(true);
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
  await waitFor(async () => expect((await templateRadio()).checked).toBe(true));
  try {
    setApiBase("https://other-backend.test");
    view.rerender(<PlaygroundWorkspace />);
    await waitFor(async () => expect((await templateRadio()).checked).toBe(false));
  } finally {
    setApiBase(original);
  }
});
