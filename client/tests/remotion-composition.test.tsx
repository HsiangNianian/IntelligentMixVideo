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

/** 通过实际下拉控件选项切换画布或帧率。 */
async function choose(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), {
    key: "ArrowDown",
  });
  fireEvent.keyDown(await screen.findByRole("option", { name: option }), {
    key: "Enter",
  });
}

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

// 不调整设置时显式发送默认画布、帧率和帧数，避免客户端与服务端默认值漂移。
test("默认生成配置随首次文字请求提交", async () => {
  await workspace();
  expect(screen.getByText("1080×1920 · 30 FPS · 5 秒（150 帧）")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0]).toEqual({
    description: "生成标题",
    composition: {
      width: 1080,
      height: 1920,
      fps: 30,
      duration_in_frames: 150,
    },
  });
});

// 修改预设、时长和帧率后传递实际整帧值；后续对话不重新覆盖画布。
test("横屏与 24 FPS 将小数秒取整到帧", async () => {
  await workspace();
  await choose("画布", "横屏 16:9");
  fireEvent.change(screen.getByLabelText("字效时长（秒）"), {
    target: { value: "3.2" },
  });
  fireEvent.click(screen.getByText("高级设置（分辨率、帧率）"));
  await choose("帧率", "24 FPS");
  expect(
    screen.getByText("1920×1080 · 24 FPS · 3.208 秒（77 帧）"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0].composition).toEqual({
    width: 1920,
    height: 1080,
    fps: 24,
    duration_in_frames: 77,
  });
});

// 自定义分辨率与快捷时长可以组合；开始创建后所有配置入口同步锁定。
test("自定义画布提交期间锁定且新增会话恢复默认", async () => {
  remotionServer((path) =>
    path === "/works" ? new Promise<Response>(() => {}) : undefined,
  );
  await act(async () => {
    render(<RemotionWorkspace />);
  });
  await choose("画布", "自定义");
  fireEvent.change(screen.getByLabelText("画布宽度（像素）"), {
    target: { value: "1280" },
  });
  fireEvent.change(screen.getByLabelText("画布高度（像素）"), {
    target: { value: "720" },
  });
  fireEvent.click(screen.getByRole("button", { name: "8 秒" }));
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "字效" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0].composition).toEqual({
    width: 1280,
    height: 720,
    fps: 30,
    duration_in_frames: 240,
  });
  for (const label of [
    "画布",
    "画布宽度（像素）",
    "画布高度（像素）",
    "帧率",
    "字效时长（秒）",
  ])
    expect(screen.getByLabelText(label).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("字效时长（秒）"), {
    target: { value: "3" },
  });
  expect(screen.getByLabelText<HTMLInputElement>("字效时长（秒）").value).toBe(
    "8",
  );
  fireEvent.click(screen.getByRole("button", { name: "新增聊天" }));
  expect(screen.getByText("1080×1920 · 30 FPS · 5 秒（150 帧）")).toBeTruthy();
  expect(screen.getByLabelText("字效时长（秒）").hasAttribute("disabled")).toBe(
    false,
  );
});

// 无效中间输入阻止按钮和 Enter 提交，修正后原描述草稿仍可发送。
test.each([
  ["字效时长（秒）", ""],
  ["字效时长（秒）", "0"],
  ["字效时长（秒）", "31"],
  ["字效时长（秒）", "0.001"],
  ["画布宽度（像素）", "63"],
  ["画布宽度（像素）", "1081"],
  ["画布高度（像素）", "4000"],
])("非法生成配置不发送：%s = %s", async (label, value) => {
  await workspace();
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(true);
  fireEvent.keyDown(screen.getByLabelText("字效描述"), { key: "Enter" });
  expect(creations()).toHaveLength(0);
  expect(screen.getByLabelText<HTMLTextAreaElement>("字效描述").value).toBe(
    "生成标题",
  );
  fireEvent.change(screen.getByLabelText(label), {
    target: { value: label.includes("时长") ? "5" : "1080" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
});

// 像素总数是独立限制；合法的单边尺寸组合仍可能超出画布预算。
test("超大像素画布不提交，4K 与 30 秒 60 FPS 边界可用", async () => {
  await workspace();
  await choose("画布", "自定义");
  for (const label of ["画布宽度（像素）", "画布高度（像素）"])
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: "3840" },
    });
  expect(screen.getByRole("alert").textContent).toContain("8,294,400");
  fireEvent.change(screen.getByLabelText("画布高度（像素）"), {
    target: { value: "2160" },
  });
  fireEvent.change(screen.getByLabelText("字效时长（秒）"), {
    target: { value: "30" },
  });
  await choose("帧率", "60 FPS");
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(creations()).toHaveLength(1));
  expect(creations()[0].composition).toEqual({
    width: 3840,
    height: 2160,
    fps: 60,
    duration_in_frames: 1800,
  });
});

// 图片上传期间使用提交瞬间的配置；失败后保留选择且不自动重试写入。
test("图片上传锁定配置，失败后可重新编辑", async () => {
  let finish: (response: Response) => void = () => {};
  remotionServer((path) =>
    path === "/assets"
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : undefined,
  );
  const { result } = renderHook(() => useTemplateSession(() => {}));
  act(() => {
    result.current.configure({ ...defaultComposition(), seconds: "8" });
    result.current.send(
      "",
      new File(["image"], "test.png", { type: "image/png" }),
    );
    result.current.configure({ ...defaultComposition(), seconds: "3" });
  });
  expect(result.current.compositionDraft.seconds).toBe("8");
  await act(async () => finish(new Response(null, { status: 503 })));
  await waitFor(() => expect(result.current.busy).toBeNull());
  expect(creations()).toHaveLength(0);
  expect(result.current.compositionDraft.seconds).toBe("8");
  act(() =>
    result.current.configure({ ...defaultComposition(), seconds: "3" }),
  );
  expect(result.current.compositionDraft.seconds).toBe("3");
});

// 绕过 UI 调用时仍校验配置，不能产生聊天消息、上传或创建任务。
test("会话核心拒绝非法配置且图片首轮携带合法配置", async () => {
  remotionServer((path) =>
    path === "/assets" ? Response.json({ id: "asset-1" }) : undefined,
  );
  const { result } = renderHook(() => useTemplateSession(() => {}));
  const image = new File(["image"], "test.png", { type: "image/png" });
  act(() => {
    result.current.configure({ ...defaultComposition(), fps: "NaN" });
    result.current.send("", image);
  });
  expect(result.current.messages).toHaveLength(0);
  expect(fetchMock.mock.calls).toHaveLength(0);
  act(() => {
    result.current.configure({
      ...defaultComposition(),
      canvas: "square",
      width: "1080",
      height: "1080",
      fps: "25",
    });
    result.current.send("", image);
  });
  await waitFor(() => expect(result.current.version).not.toBeNull());
  expect(creations()[0]).toEqual({
    image: { asset_id: "asset-1" },
    composition: {
      width: 1080,
      height: 1080,
      fps: 25,
      duration_in_frames: 125,
    },
  });
  act(() =>
    result.current.configure({ ...defaultComposition(), seconds: "3" }),
  );
  expect(result.current.compositionDraft.seconds).toBe("5");
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
