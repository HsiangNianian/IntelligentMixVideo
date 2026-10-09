/** 固定画布生成协议测试：首次请求、上传互斥、会话隔离及历史实际规格；执行 bun run test。 */
import { expect, test } from "bun:test";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { remotionServer } from "./remotion-server";
import { remotionJob, remotionVersion } from "./remotion-fixtures";
import { fetchMock } from "./setup";

/** 仅提取创建任务的写请求，不把历史列表读取算成重复提交。 */
function creations() {
  return fetchMock.mock.calls
    .filter(([url, options]) => String(url).endsWith("/works") && options?.method === "POST")
    .map(([, options]) => JSON.parse(String(options?.body)));
}

// 画布直接钉死：不提供任何画布、帧率或时长入口，只显示固定规格说明。
test("新会话钉死固定画布并发送固定初始配置", async () => {
  remotionServer(path => path === "/works"
    ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
    : undefined);
  await act(async () => { render(<RemotionWorkspace />); });
  expect(screen.getByLabelText("生成配置").textContent).toBe("1080×1920 · 30 FPS");
  for (const name of ["画布", "帧率", "Sprite 类型"]) {
    expect(screen.queryByRole("combobox", { name })).toBeNull();
  }
  expect(screen.queryByLabelText("字效时长（秒）")).toBeNull();
  expect(screen.queryByLabelText("画布宽度（像素）")).toBeNull();
  expect(screen.queryByLabelText("画布高度（像素）")).toBeNull();
  fireEvent.change(screen.getByLabelText("字效描述"), { target: { value: "生成标题" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0]).toEqual({
    description: "生成标题",
    composition: { width: 1080, height: 1920, fps: 30, duration_in_frames: 150 },
  });
  // 创建任务挂起时发送入口被停止替换，图片入口同步锁定，避免重复提交与换图。
  expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
  expect(screen.getByRole("button", { name: "停止" })).toBeTruthy();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "图片" }).disabled).toBe(true);
});

// 没有用户输入时不能创建任务；删除可变表单不绕过已有发送条件。
test("空白描述不创建任务", () => {
  remotionServer();
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("  "));
  expect(creations()).toHaveLength(0);
  expect(result.current.messages).toHaveLength(0);
});

// 上传未完成前只能持有一条写请求；失败后允许用户显式重试原图。
test("图片上传互斥且失败后可重试固定配置", async () => {
  let finish!: (response: Response) => void;
  remotionServer(path => path === "/assets"
    ? new Promise<Response>(resolve => { finish = resolve; })
    : undefined);
  const { result } = renderHook(() => useTemplateSession(() => {}));
  const image = new File(["image"], "test.png", { type: "image/png" });
  act(() => {
    result.current.send("", image);
    result.current.send("重复上传", image);
  });
  expect(result.current.busy).toBe("chat");
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/assets"))).toHaveLength(1);
  expect(creations()).toHaveLength(0);
  await act(async () => finish(new Response(null, { status: 503 })));
  await waitFor(() => expect(result.current.busy).toBeNull());
  expect(creations()).toHaveLength(0);
  act(() => result.current.send("", image));
  await act(async () => finish(Response.json({ id: "asset-1" })));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  expect(creations()).toEqual([{
    image: { asset_id: "asset-1" },
    composition: { width: 1080, height: 1920, fps: 30, duration_in_frames: 150 },
  }]);
});

// 切换到空白会话后，旧上传回执不能悄悄创建旧请求或污染新会话。
test("迟到上传不跨会话创建作品", async () => {
  let finish!: (response: Response) => void;
  remotionServer(path => path === "/assets"
    ? new Promise<Response>(resolve => { finish = resolve; })
    : undefined);
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("旧图片", new File(["image"], "test.png", { type: "image/png" })));
  act(() => result.current.reset());
  await act(async () => finish(Response.json({ id: "asset-old" })));
  expect(creations()).toHaveLength(0);
  expect(result.current.workId).toBeNull();
  expect(result.current.messages).toHaveLength(0);
  act(() => result.current.send("新会话"));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0]).toEqual({
    description: "新会话",
    composition: { width: 1080, height: 1920, fps: 30, duration_in_frames: 150 },
  });
});

// 已有成功结果的实际时长只属于该会话，新会话重新发送独立初始规格。
test("新增会话不沿用上一次组合的时长", async () => {
  remotionServer(path => {
    if (path === "/versions/version-1") {
      const version = remotionVersion();
      version.spec.composition.duration_in_frames = 420;
      return Response.json(version);
    }
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("十四秒组合"));
  await waitFor(() => expect(result.current.version?.spec.composition.duration_in_frames).toBe(420));
  act(() => result.current.reset());
  act(() => result.current.send("新的组合"));
  await waitFor(() => expect(creations()).toHaveLength(2));
  expect(creations()[1].composition).toEqual({ width: 1080, height: 1920, fps: 30, duration_in_frames: 150 });
});

// 恢复历史时只展示成功版本的事实配置，不用当前默认值覆盖历史记录。
test("成功版本配置从服务端恢复，后续消息不携带生成配置", async () => {
  remotionServer((path) => {
    if (path === "/versions/version-1") {
      const version = remotionVersion();
      version.spec.composition = {
        width: 1280,
        height: 720,
        fps: 25,
        duration_in_frames: 200,
      };
      return Response.json(version);
    }
    if (path.endsWith("/messages"))
      return Response.json(remotionJob("answered"));
  });
  const { result, unmount } = renderHook(() => useTemplateSession(() => {}));
  act(() => result.current.send("生成"));
  await waitFor(() => expect(result.current.version).not.toBeNull());
  act(() => result.current.send("你好"));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/messages")),
    ).toBe(true),
  );
  const edit = fetchMock.mock.calls.find(([url]) =>
    String(url).endsWith("/messages"),
  )!;
  expect(JSON.parse(String(edit[1]?.body))).not.toHaveProperty("composition");
  unmount();
  render(<RemotionWorkspace />);
  expect((await screen.findByLabelText("成功版本配置")).textContent).toBe(
    "1280×720 · 25 FPS · 8 秒（200 帧）",
  );
  expect(screen.queryByRole("region", { name: "生成配置" })).toBeNull();
});
