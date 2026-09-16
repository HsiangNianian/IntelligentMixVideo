/** Remotion 工作区行为测试：真实表单、HTTP 协议、草稿验收、会话隔离与复制；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StrictMode } from "react";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { backgroundUrl, validValue } from "@/features/remotion_templates/model";
import type { Job, Values } from "@/features/remotion_templates/model";
import { fetchMock } from "./setup";
import { remotionJob, remotionVersion } from "./remotion-fixtures";

import { remotionServer as server } from "./remotion-server";

// 首次消息请求尚未返回、没有播放器时，只锁定操作，不显示预览渲染遮罩。
test("消息等待不冒充预览渲染", async () => {
  server((path) =>
    path === "/works" ? new Promise<Response>(() => {}) : undefined,
  );
  await act(async () => {
    render(<RemotionWorkspace />);
  });
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "你能做什么？" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  expect(screen.queryByTitle("Remotion 字效播放器")).toBeNull();
  expect(screen.queryByText("正在渲染预览…") !== null).toBe(false);
  expect(screen.getByText("正在处理…")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(true);
});

// 纯问答的运行和 SSE 终态保持原播放器、代码与参数，恢复会话后仍能继续修改。
test("纯问答保留成功预览并恢复历史", async () => {
  const fake = server((path) =>
    path.endsWith("/messages")
      ? Response.json({ ...remotionJob("running"), id: "question-1" })
      : undefined,
  );
  const view = render(<RemotionWorkspace />);
  await generate();
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const src = iframe.src;
  const post = spyOn(iframe.contentWindow!, "postMessage");
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "当前字号是多少？" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() =>
    expect(fake.snapshots.get("work-1")?.job.id).toBe("question-1"),
  );
  await waitFor(() => expect(fake.streams.size).toBe(1));
  expect(screen.queryByText("正在渲染预览…") !== null).toBe(false);
  expect(screen.getByTitle("Remotion 字效播放器")).toBe(iframe);
  expect(iframe.src).toBe(src);
  expect(post.mock.calls).toHaveLength(0);
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "把字号改大" },
  });
  await act(async () =>
    fake.advance({
      ...remotionJob("answered"),
      id: "question-1",
      message: "当前字号是 64px。",
    }),
  );
  await screen.findByText("当前字号是 64px。");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(false);
  expect(iframe.src).toBe(src);
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(false);
  view.unmount();
  render(<RemotionWorkspace />);
  await screen.findByText("当前字号是 64px。");
  await previewReady();
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "把字号改大" },
  });
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(
    screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
  ).toContain("version-1");
});

// 首轮问答没有成功版本也能结束并继续发送，不永久等待一个不存在的播放器。
test("首次问答结束后可继续生成", async () => {
  const fake = server((path) =>
    path === "/works"
      ? Response.json({ work: { id: "work-1" }, job: remotionJob("running") })
      : path.endsWith("/messages")
        ? Response.json({ ...remotionJob(), id: "generation-1" })
        : undefined,
  );
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "你能做什么？" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () =>
    fake.advance({ ...remotionJob("answered"), message: "可以制作字效。" }),
  );
  await screen.findByText("可以制作字效。");
  expect(screen.queryByTitle("Remotion 字效播放器")).toBeNull();
  expect(screen.queryByText("正在渲染预览…") !== null).toBe(false);
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "制作一个标题" },
  });
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await screen.findByTitle("Remotion 字效播放器");
  await previewReady();
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(false);
});

/** 从输入表单发起首次生成，并等待成功版本的代码进入浮板。 */
async function generate(ready = true) {
  fireEvent.change(screen.getByRole("textbox", { name: "字效描述" }), {
    target: { value: "制作今日灵感标题" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  if (ready) await previewReady();
}

/** 模拟隔离播放器首帧确认；Happy DOM 不加载真实 iframe。 */
async function previewReady() {
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  await waitFor(() => expect(iframe.src.includes("#")).toBe(true));
  const channel = new URL(iframe.src).hash.slice(1);
  const post = spyOn(iframe.contentWindow!, "postMessage");
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: { type: "imv-preview-ready", channel },
      }),
    );
  });
  const requestId = post.mock.calls.at(-1)?.[0].requestId;
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: { type: "imv-preview-rendered", channel, requestId },
      }),
    );
  });
  post.mockRestore();
}

// 成功版本同时驱动代码、参数与播放器，复制使用带默认值的导出；新增清空整个会话。
test("生成、复制并新增独立会话", async () => {
  server();
  const clipboard = spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  render(<RemotionWorkspace />);
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(true);
  await generate();
  expect(screen.getByLabelText<HTMLInputElement>("文字").value).toBe(
    "今日灵感",
  );
  expect(screen.getByTitle("Remotion 字效播放器").getAttribute("sandbox")).toBe(
    "allow-scripts",
  );
  fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
  await screen.findByText("已复制");
  expect(clipboard.mock.calls[0][0]).toContain('return "今日灵感"');
  fireEvent.change(screen.getByRole("textbox", { name: "背景视频直链" }), {
    target: { value: "https://media.test/video.mp4" },
  });
  fireEvent.click(screen.getByRole("button", { name: "新增" }));
  expect(
    within(screen.getByRole("log")).queryByText("制作今日灵感标题") === null,
  ).toBe(true);
  expect(screen.queryByTitle("Remotion 字效播放器")).toBeNull();
  expect(screen.getByLabelText<HTMLInputElement>("背景视频直链").value).toBe(
    "",
  );
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(true);
});

// 澄清答案通过统一 messages 接口绑定提出问题的 job，普通修改不会伪装成答案。
test("澄清问题绑定当前任务，回答后生成成功模板", async () => {
  let body: Record<string, unknown> = {};
  server((path, options) => {
    if (path === "/works")
      return Response.json({
        work: { id: "work-1" },
        job: remotionJob("needs_input"),
      });
    if (path.endsWith("/messages")) {
      body = JSON.parse(String(options?.body));
      return Response.json(remotionJob());
    }
  });
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "帮我制作标题" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await screen.findByText("标题写什么？");
  await generate();
  expect(body.reply_to_job_id).toBe("job-1");
  expect(body).not.toHaveProperty("parameters");
});

// 参数修改立即锁定控件，只提交首个合法值；验收完成后展示新代码。
test("参数调整锁定控件，成功后才能复制新代码", async () => {
  let submitted: Values = {};
  let resolve: ((response: Response) => void) | undefined;
  server((path, options) => {
    if (path.endsWith("/messages")) {
      submitted = JSON.parse(String(options?.body)).parameters;
      return new Promise<Response>((done) => {
        resolve = done;
      });
    }
    if (path === "/versions/version-2")
      return Response.json(remotionVersion("version-2", submitted));
    if (path === "/versions/version-2/artifacts/Export.tsx")
      return new Response("export const size = 72;");
  });
  render(<RemotionWorkspace />);
  await generate();
  fireEvent.change(screen.getByLabelText("字号滑块"), {
    target: { value: "72" },
  });
  fireEvent.pointerUp(screen.getByLabelText("字号滑块"));
  fireEvent.change(screen.getByLabelText("字号滑块"), {
    target: { value: "88" },
  });
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("72");
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(true);
  await waitFor(() => expect(submitted.size).toBe(72), { timeout: 2000 });
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(1);
  await act(async () =>
    resolve!(Response.json(remotionJob("succeeded", "version-2"))),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain(
    "size = 72",
  );
});

// 参数不通过时恢复成功版本，失败候选和待验收参数不能覆盖可复制代码。
test("参数验收失败恢复原值并保留成功代码", async () => {
  server((path) =>
    path.endsWith("/messages")
      ? Response.json(remotionJob("failed"))
      : undefined,
  );
  render(<RemotionWorkspace />);
  await generate();
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "90" } });
  fireEvent.blur(screen.getByLabelText("字号"));
  await screen.findAllByText(
    "本次未能完成模板，请重试；已有结果仍可使用。",
    {},
    { timeout: 2000 },
  );
  await waitFor(() =>
    expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("64"),
  );
  expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain(
    "今日灵感",
  );
  expect(
    screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
  ).toBe(false);
});

// 创建请求尚未返回就新增，旧任务留在历史但迟到结果不能回填新聊天。
test("新增隔离迟到的创建响应并保留旧任务", async () => {
  let resolve: ((response: Response) => void) | undefined;
  server((path) => {
    if (path === "/works")
      return new Promise<Response>((done) => {
        resolve = done;
      });
    if (path.endsWith("/cancel"))
      return Response.json(remotionJob("cancelled"));
  });
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "旧会话" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() => expect(resolve).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "新增" }));
  await act(async () =>
    resolve!(
      Response.json({ work: { id: "work-1" }, job: remotionJob("running") }),
    ),
  );
  await screen.findByRole("button", { name: /旧会话/ });
  expect(
    fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/cancel")),
  ).toBe(false);
  expect(screen.queryByTitle("Remotion 字效播放器") === null).toBe(true);
  expect(within(screen.getByRole("log")).queryByText("旧会话") === null).toBe(
    true,
  );
});

// 预览只信任当前 iframe 的通道；修改背景链接不创建新生成任务。
test("播放器握手隔离和背景直链更新", async () => {
  server();
  render(<RemotionWorkspace />);
  await generate(false);
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const channel = new URL(iframe.src).hash.slice(1);
  const post = spyOn(iframe.contentWindow!, "postMessage");
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window,
        data: { type: "imv-preview-ready", channel },
      }),
    );
  });
  expect(post).not.toHaveBeenCalled();
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: { type: "imv-preview-ready", channel },
      }),
    );
  });
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(true);
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: {
          type: "imv-preview-rendered",
          channel,
          requestId: post.mock.calls.at(-1)?.[0].requestId,
        },
      }),
    );
  });
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(false);
  fireEvent.change(screen.getByLabelText("背景视频直链"), {
    target: { value: "https://media.test/demo.mp4" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "加载" }).closest("form")!,
  );
  await waitFor(() =>
    expect(post.mock.calls.at(-1)?.[0].background).toBe(
      "https://media.test/demo.mp4",
    ),
  );
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(0);
});

// 非法颜色和非有限数不会通过参数校验，背景链接拒绝脚本、本地文件和内嵌凭据。
test("参数与背景链接的边界校验", () => {
  const controls = remotionVersion().candidate.config_schema.properties;
  expect(validValue(controls.size, NaN)).toBe(false);
  expect(validValue(controls.size, 601)).toBe(false);
  expect(validValue(controls.color, "#FFFFFF80")).toBe(true);
  expect(validValue(controls.color, "#fff")).toBe(false);
  expect(validValue(controls.text, " ")).toBe(false);
  for (const url of [
    "javascript:alert(1)",
    "file:///tmp/video.mp4",
    "https://user:secret@host/video.mp4",
  ])
    expect(() => backgroundUrl(url)).toThrow();
  expect(backgroundUrl("")).toBe("");
});

// 卸载清理 SSE 和计时器，但不能取消服务端任务。
test("卸载清理订阅并保留任务", async () => {
  const job: Job = remotionJob("running");
  const fake = server((path) => {
    if (path === "/works")
      return Response.json({ work: { id: "work-1" }, job });
    if (path.endsWith("/cancel"))
      return Response.json(remotionJob("cancelled"));
  });
  const clear = spyOn(window, "clearTimeout");
  const view = render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "测试卸载" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await screen.findByRole("button", { name: "停止" });
  await waitFor(() => expect(fake.streams.size).toBe(1));
  view.unmount();
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/cancel")),
    ).toBe(false),
  );
  await waitFor(() => expect(fake.streams.size).toBe(0));
  expect(clear).toHaveBeenCalled();
});

// StrictMode 重复挂载后仍能建立 SSE，成功事件可更新预览。
test("StrictMode 下仍能通过 SSE 收到成功结果", async () => {
  const fake = server((path) => {
    if (path === "/works")
      return Response.json({
        work: { id: "work-1" },
        job: remotionJob("running"),
      });
  });
  render(
    <StrictMode>
      <RemotionWorkspace />
    </StrictMode>,
  );
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "开发模式标题" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () => fake.advance(remotionJob()));
  await waitFor(
    () =>
      expect(
        screen
          .getByRole("button", { name: "复制代码" })
          .hasAttribute("disabled"),
      ).toBe(false),
    { timeout: 2500 },
  );
});

// 渲染中禁止第二次参数修改、图片变更和发送；文字草稿保留，终态后恢复交互。
test("渲染期间锁定所有输入操作，完成后恢复", async () => {
  let finish: ((response: Response) => void) | undefined;
  let submitted: Values = {};
  server((path, options) => {
    if (path.endsWith("/messages")) {
      submitted = JSON.parse(String(options?.body)).parameters;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    if (path === "/versions/version-2")
      return Response.json(remotionVersion("version-2", submitted));
  });
  render(<RemotionWorkspace />);
  await generate();
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "72" } });
  fireEvent.blur(screen.getByLabelText("字号"));
  await waitFor(() => expect(finish).toBeDefined(), { timeout: 2000 });
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(true);
  expect(screen.getByLabelText("上传参考图片").hasAttribute("disabled")).toBe(
    true,
  );
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "96" } });
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "待发送草稿" },
  });
  fireEvent.keyDown(screen.getByLabelText("字效描述"), { key: "Enter" });
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(1);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("72");
  await act(async () =>
    finish!(Response.json(remotionJob("succeeded", "version-2"))),
  );
  await waitFor(() =>
    expect(
      screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
    ).toContain("version-2"),
  );
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(true);
  await previewReady();
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(false);
  expect(screen.getByLabelText<HTMLTextAreaElement>("字效描述").value).toBe(
    "待发送草稿",
  );
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(false);
});

// 预览失败必须解除锁定；旧请求的完成消息不能释放新背景的加载锁。
test("背景加载过滤迟到确认，失败后解除锁定", async () => {
  server();
  render(<RemotionWorkspace />);
  await generate();
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const channel = new URL(iframe.src).hash.slice(1);
  const post = spyOn(iframe.contentWindow!, "postMessage");
  fireEvent.change(screen.getByLabelText("背景视频直链"), {
    target: { value: "https://media.test/video.mp4" },
  });
  fireEvent.click(screen.getByRole("button", { name: "加载" }));
  const requestId = post.mock.calls.at(-1)?.[0].requestId;
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(true);
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: {
          type: "imv-preview-rendered",
          channel,
          requestId: requestId - 1,
        },
      }),
    );
  });
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(true);
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: {
          type: "imv-preview-error",
          channel,
          requestId,
          message: "背景加载失败",
        },
      }),
    );
  });
  expect(screen.getByText("背景加载失败")).toBeTruthy();
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(false);
});

// 图片输入至少可单独创建任务，上传返回的素材 ID 用于生成；背景链接不发送给模型。
test("纯图片生成使用上传 ID，背景链接留在客户端", async () => {
  let uploaded = false;
  let created: Record<string, unknown> = {};
  server((path, options) => {
    if (path === "/assets") {
      uploaded = options?.body instanceof FormData;
      return Response.json({ id: "image-1" });
    }
    if (path === "/works") {
      created = JSON.parse(String(options?.body));
      return Response.json({ work: { id: "work-1" }, job: remotionJob() });
    }
  });
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("背景视频直链"), {
    target: { value: "https://media.test/background.mp4" },
  });
  fireEvent.change(screen.getByLabelText("上传参考图片"), {
    target: {
      files: [new File(["fixture"], "reference.png", { type: "image/png" })],
    },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "发送" }).closest("form")!,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(uploaded).toBe(true);
  expect(created).toEqual({
    image: { asset_id: "image-1" },
    composition: {
      width: 1080,
      height: 1920,
      fps: 30,
      duration_in_frames: 150,
    },
  });
});

// 超限图片立即拒绝，剪贴板拒绝访问时保留代码供手动复制。
test("图片限制与剪贴板失败反馈", async () => {
  server();
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("上传参考图片"), {
    target: {
      files: [new File(["gif"], "reference.gif", { type: "image/gif" })],
    },
  });
  expect(
    screen.getByText("请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片。"),
  ).toBeTruthy();
  await generate();
  spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("denied"),
  );
  fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
  await screen.findByText("复制失败，请展开代码后手动选择复制。");
});

// 参数写入未获确认时不自动重发，恢复上个成功值，避免失败后永久锁定表单。
test("参数提交网络失败恢复可编辑状态", async () => {
  server((path) => {
    if (path.endsWith("/messages")) throw new TypeError("offline");
  });
  render(<RemotionWorkspace />);
  await generate();
  fireEvent.change(screen.getByLabelText("字号"), { target: { value: "90" } });
  fireEvent.blur(screen.getByLabelText("字号"));
  await screen.findByText(
    /无法连接服务端，请确认服务已启动。/,
    {},
    { timeout: 2000 },
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新任务" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "刷新任务" }) === null).toBe(
      true,
    ),
  );
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const channel = new URL(iframe.src).hash.slice(1);
  // 本地参数回退也需要绘制确认；显式模拟失败出口，释放该次预览锁。
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: iframe.contentWindow,
        data: { type: "imv-preview-error", channel, message: "测试渲染失败" },
      }),
    );
  });
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("64");
  expect(screen.getByLabelText("字号").hasAttribute("disabled")).toBe(false);
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(1);
});

// 会话快照读取失败允许手动恢复，不重新提交生成请求。
test("刷新会话恢复读取而不重复生成", async () => {
  let reads = 0;
  const fake = server((path) => {
    if (path === "/works")
      return Response.json({
        work: { id: "work-1" },
        job: remotionJob("running"),
      });
    if (path === "/works/work-1/session") {
      if (++reads === 1) throw new TypeError("offline");
      return undefined;
    }
  });
  render(<RemotionWorkspace />);
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "恢复测试" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByRole("button", { name: "刷新任务" }, { timeout: 2500 });
  expect(
    screen.getByRole("button", { name: "发送" }).hasAttribute("disabled"),
  ).toBe(true);
  fake.advance(remotionJob());
  fireEvent.click(screen.getByRole("button", { name: "刷新任务" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "复制代码" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(
    fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/works")),
  ).toHaveLength(1);
});

// 输入完整文字和数字后再提交，避免首个字符立即触发渲染并锁住后续输入。
test("参数文字允许连续编辑并在 Enter 后提交", async () => {
  let submitted: Values = {};
  server((path, options) => {
    if (path.endsWith("/messages")) {
      submitted = JSON.parse(String(options?.body)).parameters;
      return Response.json(remotionJob("failed"));
    }
  });
  render(<RemotionWorkspace />);
  await generate();
  const text = screen.getByLabelText("文字");
  fireEvent.change(text, { target: { value: "新" } });
  fireEvent.change(text, { target: { value: "新的标题" } });
  expect(text.hasAttribute("disabled")).toBe(false);
  expect(
    fetchMock.mock.calls.filter((call) =>
      String(call[0]).endsWith("/messages"),
    ),
  ).toHaveLength(0);
  fireEvent.keyDown(text, { key: "Enter" });
  expect(text.hasAttribute("disabled")).toBe(true);
  await waitFor(() => expect(submitted.text).toBe("新的标题"), {
    timeout: 2000,
  });
});

// 持续 404/503 不能堵塞后续 SSE；保留旧代码可复制，读取重试不重复提交生成。
test.each([404, 503])("新版 Export 持续 %s 时保留结果并继续会话", async (status) => {
  let available = false;
  const fake = server((path) => {
    if (path === "/versions/version-2") return Response.json(remotionVersion("version-2", {size: 72}));
    if (path === "/versions/version-2/artifacts/Export.tsx")
      return available ? new Response("export const size = 72;") : new Response(null, {status});
  });
  const view = render(<RemotionWorkspace />);
  await generate();
  const frame = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const src = frame.src;
  const oldCode = screen.getByLabelText("模板 TSX 代码").textContent;
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () => fake.advance({...remotionJob("succeeded", "version-2"), id: "edit-2"}));
  await screen.findByText(/新版本读取失败/);
  expect(screen.getByRole("button", {name: "复制代码"}).hasAttribute("disabled")).toBe(false);
  expect(screen.getByLabelText("模板 TSX 代码").textContent).toBe(oldCode);
  expect(frame.src).toBe(src);
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("64");
  await act(async () => fake.advance({...remotionJob("answered"), id: "answer-3", message: "产物失败之后的回答"}));
  await screen.findByText("产物失败之后的回答");
  fireEvent.change(screen.getByLabelText("字效描述"), {target: {value: "继续制作"}});
  expect(screen.getByRole("button", {name: "发送"}).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", {name: "重新读取结果"}));
  await screen.findByText(/新版本读取失败/);
  await waitFor(() => expect(screen.getByRole("button", {name: "复制代码"}).hasAttribute("disabled")).toBe(false));
  available = true;
  fireEvent.click(screen.getByRole("button", {name: "重新读取结果"}));
  await waitFor(() => expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain("size = 72"));
  await previewReady();
  expect(screen.queryByText(/新版本读取失败/)).toBeNull();
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("72");
  expect(fetchMock.mock.calls.filter(call => call[1]?.method === "POST")).toHaveLength(1);
  view.unmount();
  expect(fake.streams.size).toBe(0);
});

// Enter 或点击恢复前的失焦先排队参数提交，读取恢复必须等该操作结束才可执行。
test.each(["Enter", "blur"])("参数%s提交期间不能读取恢复旧基线", async (trigger) => {
  let available = false;
  let finish: ((response: Response) => void) | undefined;
  const submitted: {base_version_id: string; parameters: Values}[] = [];
  const fake = server((path, options) => {
    if (path === "/versions/version-2")
      return Response.json(remotionVersion("version-2", {text: "新标题", size: 72}));
    if (path === "/versions/version-2/artifacts/Export.tsx")
      return available ? new Response('export const title = "新标题";') : new Response(null, {status: 404});
    if (path.endsWith("/messages")) {
      submitted.push(JSON.parse(String(options?.body)));
      return new Promise(resolve => { finish = resolve; });
    }
  });
  render(<RemotionWorkspace />);
  await generate();
  await waitFor(() => expect(fake.streams.size).toBe(1));
  await act(async () => fake.advance({...remotionJob("succeeded", "version-2"), id: "edit-2"}));
  const retry = await screen.findByRole<HTMLButtonElement>("button", {name: "重新读取结果"});
  const snapshots = fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/session")).length;
  available = true;
  const size = screen.getByLabelText("字号");
  fireEvent.change(size, {target: {value: "80"}});
  if (trigger === "Enter") fireEvent.keyDown(size, {key: "Enter"});
  else fireEvent.blur(size);
  fireEvent.click(retry);
  expect(retry.disabled).toBe(true);
  expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/session"))).toHaveLength(snapshots);
  await waitFor(() => expect(submitted).toHaveLength(1), {timeout: 2000});
  expect(retry.disabled).toBe(true);
  expect(submitted[0].base_version_id).toBe("version-1");
  expect(submitted[0].parameters).toMatchObject({size: 80, text: "今日灵感"});
  // 该操作结束后的快照恢复才接收 v2；延迟任务不得再次覆盖新标题。
  await act(async () => finish!(Response.json({...remotionJob("answered"), id: "edit-3", message: "保留现状"})));
  await waitFor(() => expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain("新标题"));
  await previewReady();
  await act(async () => new Promise(resolve => setTimeout(resolve, 700)));
  expect(screen.getByLabelText<HTMLInputElement>("文字").value).toBe("新标题");
  expect(screen.getByLabelText<HTMLInputElement>("字号").value).toBe("72");
  expect(submitted).toHaveLength(1);
});
