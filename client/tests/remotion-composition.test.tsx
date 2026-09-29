/** 生成配置行为测试：表单、整帧换算、HTTP 载荷、上传锁与会话隔离；执行 bun run test。 */
import { expect, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { defaultComposition } from "@/features/remotion_templates/composition";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { remotionServer } from "./remotion-server";
import { remotionJob, remotionVersion } from "./remotion-fixtures";
import { fetchMock } from "./setup";

/** 首轮请求保持运行，避免真实模型调用或播放器加载干扰配置测试。 */
async function workspace() {
  remotionServer((path) =>
    path === "/works"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : undefined,
  );
  await act(async () => {
    render(<RemotionWorkspace />);
  });
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "生成标题" },
  });
}

/** 仅提取创建任务的写请求，不把历史列表读取算成重复提交。 */
function creations() {
  return fetchMock.mock.calls
    .filter(
      ([url, options]) =>
        String(url).endsWith("/works") && options?.method === "POST",
    )
    .map(([, options]) => JSON.parse(String(options?.body)));
}

// 新会话只能提交固定主画布，最终时长由组合实例确定。
test("新会话显示固定画布并发送默认初始配置", async () => {
  await workspace();
  expect(screen.getByText("竖屏 1080×1920 · 30 FPS")).toBeTruthy();
  expect(screen.queryByRole("combobox", {name: "画布"})).toBeNull();
  expect(screen.queryByRole("combobox", {name: "Sprite 类型"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "发送"}));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0].composition).toEqual({width:1080,height:1920,fps:30,duration_in_frames:150});
});

// 绕过界面传入非固定画布时，不能上传图片或创建任务。
test("核心拒绝非固定画布并允许恢复默认后上传", async () => {
  remotionServer(path => path === "/assets" ? Response.json({id:"asset-1"}) : undefined);
  const {result} = renderHook(() => useTemplateSession(() => {}));
  const image = new File(["image"], "test.png", {type:"image/png"});
  act(() => { result.current.configure({...defaultComposition(), height:"1080"}); result.current.send("", image); });
  expect(creations()).toHaveLength(0);
  expect(result.current.messages).toHaveLength(0);
  act(() => { result.current.configure(defaultComposition()); result.current.send("", image); });
  await waitFor(() => expect(result.current.version).not.toBeNull());
  expect(creations()[0].image).toEqual({asset_id:"asset-1"});
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
