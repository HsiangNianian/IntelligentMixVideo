/** 成功版本交互回归：目录、折叠复制、只读预览、导航保护与并发清理；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { PublishSpriteRequestSchema, PublishSpriteResponseSchema, ListSpritesResponseSchema, SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import { useTemplateSession } from "@/features/remotion_templates/useTemplateSession";
import { useTemplateVersions } from "@/features/remotion_templates/useTemplateVersions";
import * as api from "@/features/remotion_templates/api";
import { apply } from "@/features/remotion_templates/localSessions";
import { remotionJob, remotionVersion } from "./remotion-fixtures";
import { remotionServer, seed } from "./remotion-server";
import { codeText } from "./remotion-version-helpers";
import { FakeEventSource, fetchMock } from "./setup";

/** 建立两个真实公开成功任务并写入本地会话，刷新恢复时可把版本放回对应的助手消息。 */
async function history(
  overrides: Parameters<typeof remotionServer>[0] = () => undefined,
) {
  const fake = remotionServer(overrides);
  await seed("最初标题");
  fake.advance({ ...remotionJob("succeeded", "version-2"), id: "job-2" });
  await apply("work-1", fake.records);
  localStorage.setItem(`imv.remotion.selected:${api.apiUrl("")}`, "work-1");
  return fake;
}

/** Happy DOM 不执行 iframe，按真实协议模拟首帧就绪，仅验证父页面行为。 */
async function ready() {
  const frame = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  await act(async () =>
    window.dispatchEvent(
      new MessageEvent("message", {
        source: frame.contentWindow,
        data: { type: "imv-preview-ready", channel },
      }),
    ),
  );
  await waitFor(() => expect(post.mock.calls.length).toBeGreaterThan(0));
  const update = post.mock.calls.at(-1)![0];
  await act(async () =>
    window.dispatchEvent(
      new MessageEvent("message", {
        source: frame.contentWindow,
        data: {
          type: "imv-preview-rendered",
          channel,
          requestId: update.requestId,
        },
      }),
    ),
  );
  post.mockRestore();
}

/** 发布成功的最小 Protobuf 回执，不访问真实资产库。 */
function publishedAsset(sourceVersionId = "version-2") {
  return new Response(Uint8Array.from(toBinary(PublishSpriteResponseSchema, create(PublishSpriteResponseSchema, {
    sprite: { spriteId: "sprite-1", name: "今日灵感", kind: SpriteKind.TEXT, sourceVersionId },
  }))), { headers: { "Content-Type": "application/x-protobuf" } });
}

/** 资产目录：本会话的 version-2 已经保存为 sprite-1，工作区应默认提议覆盖它。 */
function spriteCatalog(name = "今日灵感") {
  return new Response(Uint8Array.from(toBinary(ListSpritesResponseSchema, create(ListSpritesResponseSchema, {
    sprites: [{
      spriteId: "sprite-1", name, kind: SpriteKind.TEXT, sourceVersionId: "version-2",
      canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 150 },
    }],
  }))), { headers: { "Content-Type": "application/x-protobuf" } });
}

// 顶部保存是唯一的保存入口，使用当前预览（含历史）版本与它卡片上选择的分类；重复点击不会重复写入，左侧仍可新建会话。
test("顶栏保存当前预览版本为资产并防止重复提交", async () => {
  const requests: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>>[] = [];
  let complete: ((response: Response) => void) | undefined;
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2]);
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywords", title: "关键词" }] };
      return Response.json(version);
    }
    if (path === "/api/sprites/publish") {
      requests.push(fromBinary(PublishSpriteRequestSchema, new Uint8Array(options!.body as Uint8Array)));
      return requests.length === 1 ? new Promise<Response>((resolve) => { complete = resolve; }) : publishedAsset();
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  expect(save.disabled).toBe(false);
  expect(screen.queryByRole("button", { name: "保存到资产" }) === null).toBe(true);
  expect(screen.queryByRole("button", { name: "新建字效会话" }) === null).toBe(true);
  expect(within(screen.getByRole("complementary", { name: "字效历史会话" })).getByRole("button", { name: "新建会话" })).toBeTruthy();
  fireEvent.click(save);
  fireEvent.click(save);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ sourceVersionId: "version-2", kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "keywords" });
  expect(save.disabled).toBe(true);
  await act(async () => complete!(publishedAsset()));
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "预览 V1" }));
  await waitFor(() => expect(screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src).toContain("version-1"));
  await ready();
  const kind = within(screen.getByLabelText("成功版本 V1")).getByRole("combobox", { name: "资产分类" });
  fireEvent.keyDown(kind, { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "滤镜" }), { key: "Enter" });
  await waitFor(() => expect(kind.textContent).toContain("滤镜"));
  fireEvent.click(save);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toMatchObject({ sourceVersionId: "version-1", kind: SpriteKind.FILTER_OVERLAY, textProp: "", keywordsProp: "" });
  await waitFor(() => expect(save.disabled).toBe(false));
});

// 模板编辑处改名后，已打开的字效工作区通过目录推送或重新聚焦同步；后台更新不覆盖名称草稿。
test("资产重命名同步到保存方式和名称提示，并保留正在输入的名称", async () => {
  let name = "今日灵感";
  await history((path) => path === "/api/sprites" ? spriteCatalog(name) : undefined);
  const view = render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const mode = await screen.findByRole("combobox", { name: "保存方式" });
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "资产名称" });
  const source = FakeEventSource.instances.find((item) => item.url.endsWith("/api/sprites/stream"))!;
  expect(mode.textContent).toContain("今日灵感");
  name = "模板编辑里的名称";
  await act(async () => source.emit("catalog.changed"));
  await waitFor(() => expect(mode.textContent).toContain(name));
  expect(input.placeholder).toBe(name);
  fireEvent.change(input, { target: { value: "尚未保存的名称" } });
  name = "另一处修改的名称";
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(mode.textContent).toContain(name));
  expect(input.value).toBe("尚未保存的名称");
  view.unmount();
  expect(source.readyState).toBe(FakeEventSource.CLOSED);
});

// 同一会话换版本不丢失名称草稿；新增会话才清空，迟到的新版本不能让已经输入的名称悄悄失效。
test("名称草稿随会话保留，切换预览版本不清空", async () => {
  await history();
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "资产名称" });
  fireEvent.change(input, { target: { value: "准备保存的名称" } });
  fireEvent.click(screen.getByRole("button", { name: "预览 V1" }));
  await waitFor(() => expect(screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src).toContain("version-1"));
  expect(input.value).toBe("准备保存的名称");
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  await waitFor(() => expect(input.value).toBe(""));
});

// 首次保存的名称可选：有内容时随同一发布请求提交；空白交给服务端使用 AI 名称，失败保留草稿可重试。
test.each(["  我的片头  ", "   "])("首次保存提交可选名称：%s", async (name) => {
  const requests: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>>[] = [];
  await history((path, options) => {
    if (path === "/api/sprites/publish") {
      requests.push(fromBinary(PublishSpriteRequestSchema, new Uint8Array(options!.body as Uint8Array)));
      return requests.length === 1
        ? Response.json({ detail: "暂时无法保存" }, { status: 503 })
        : publishedAsset();
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "资产名称" });
  fireEvent.change(input, { target: { value: name } });
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await screen.findByText("暂时无法保存");
  expect(input.value).toBe(name);
  fireEvent.click(save);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests.map((item) => [item.name, item.targetSpriteId])).toEqual([[name.trim(), ""], [name.trim(), ""]]);
  await waitFor(() => expect(input.value).toBe(""));
});

// 覆盖保存可同时填写新名称，仍然使用原资产 ID；目录读取失败时禁止降级为新建，显式重试后恢复。
test("目录失败可重试，覆盖资产时名称与目标一起提交", async () => {
  let available = false;
  let request: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>> | undefined;
  await history((path, options) => {
    if (path === "/api/sprites") return available ? spriteCatalog() : Response.json({ detail: "暂不可用" }, { status: 503 });
    if (path === "/api/sprites/publish") {
      request = fromBinary(PublishSpriteRequestSchema, new Uint8Array(options!.body as Uint8Array));
      return publishedAsset();
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  expect(save.disabled).toBe(true);
  available = true;
  fireEvent.click(await screen.findByRole("button", { name: "重试资产目录" }));
  await screen.findByRole("combobox", { name: "保存方式" });
  fireEvent.change(screen.getByRole("textbox", { name: "资产名称" }), { target: { value: "  新名字  " } });
  fireEvent.click(save);
  await waitFor(() => expect(request?.name).toBe("新名字"));
  expect(request?.targetSpriteId).toBe("sprite-1");
});

// Issue #246：本会话保存过资产后默认覆盖它；保存处可切换为另存为新资产，覆盖请求带上目标资产 ID。
test("保存资产默认覆盖本会话上一次保存的资产，也可以另存为新资产", async () => {
  const requests: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>>[] = [];
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2]);
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywords", title: "关键词" }] };
      return Response.json(version);
    }
    if (path === "/api/sprites" && (options?.method ?? "GET") === "GET") return spriteCatalog();
    if (path === "/api/sprites/publish") {
      requests.push(fromBinary(PublishSpriteRequestSchema, new Uint8Array(options!.body as Uint8Array)));
      return publishedAsset();
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const mode = await screen.findByRole("combobox", { name: "保存方式" });
  expect(mode.textContent).toContain("覆盖「今日灵感」");
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  expect(save.title).toContain("覆盖资产「今日灵感」");
  fireEvent.click(save);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({
    sourceVersionId: "version-2", targetSpriteId: "sprite-1", kind: SpriteKind.TEXT, textProp: "text", keywordsProp: "keywords",
  });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.keyDown(mode, { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "另存为新资产" }), { key: "Enter" });
  await waitFor(() => expect(mode.textContent).toContain("另存为新资产"));
  expect(save.title).toContain("保存当前预览 V2 为新资产");
  fireEvent.click(save);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].targetSpriteId).toBe("");
  expect(requests[1]).toMatchObject({ sourceVersionId: "version-2", kind: SpriteKind.TEXT });
});

// 改选其他分类表示要一个新资产：覆盖不能再改变已有片段依赖的类型，选项不可用且按卡片分类保存为新资产。
test("为版本改选其他分类时覆盖不可用，按新分类另存", async () => {
  const requests: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>>[] = [];
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2]);
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywords", title: "关键词" }] };
      return Response.json(version);
    }
    if (path === "/api/sprites" && (options?.method ?? "GET") === "GET") return spriteCatalog();
    if (path === "/api/sprites/publish") {
      requests.push(fromBinary(PublishSpriteRequestSchema, new Uint8Array(options!.body as Uint8Array)));
      return publishedAsset();
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  await screen.findByRole("combobox", { name: "保存方式" });
  const kind = within(screen.getByLabelText("成功版本 V2")).getByRole("combobox", { name: "资产分类" });
  fireEvent.keyDown(kind, { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "滤镜" }), { key: "Enter" });
  await waitFor(() => expect(kind.textContent).toContain("滤镜"));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" }).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存资产" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({ targetSpriteId: "", kind: SpriteKind.FILTER_OVERLAY });
});

// 发布被服务端拒绝时保留具体原因、不自动重试，用户再次保存后可恢复。
test("顶栏保存资产展示失败原因并允许重试", async () => {
  let attempts = 0;
  await history((path) => path === "/api/sprites/publish"
    ? ++attempts === 1 ? Response.json({ detail: "关键词字号须先设为 0" }, { status: 422 }) : publishedAsset()
    : undefined);
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  fireEvent.click(save);
  expect((await screen.findByText("关键词字号须先设为 0")).getAttribute("role")).toBe("alert");
  expect(attempts).toBe(1);
  expect(save.disabled).toBe(false);
  fireEvent.click(save);
  await waitFor(() => expect(attempts).toBe(2));
  await waitFor(() => expect(save.disabled).toBe(false));
  expect(screen.queryByText("关键词字号须先设为 0") === null).toBe(true);
});

// 关键词样式由云端模板按摆放设置：参数面板把关键词选项改成开启时先提示，保存配置被拒绝且不写服务端（本地预览不受影响）。
test("关键词选项没有保持关闭时拒绝保存配置并说明原因", async () => {
  const writes: string[] = [];
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2], { keywordSize: 0 });
      version.spec.schema_version = "2";
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywordSize", title: "关键词字号" }] };
      version.candidate.config_schema.properties.keywordSize = { type: "integer", minimum: 0, maximum: 300, title: "关键词字号" };
      return Response.json(version);
    }
    if (path === "/works/work-1/messages") {
      writes.push(String(options?.body ?? ""));
      return Response.json(remotionJob("succeeded", "version-3"));
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  expect(screen.queryByText(/关键词样式由云端模板按摆放设置/) === null).toBe(true);
  fireEvent.change(screen.getByLabelText("关键词字号"), { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("关键词样式由云端模板按摆放设置"));
  expect(screen.getByRole("alert").textContent).toContain("keywordSize 应为 0");
  expect(writes).toHaveLength(0);
  // 恢复关闭后不再提示；与默认值相同，没有净变化可保存。
  fireEvent.change(screen.getByLabelText("关键词字号"), { target: { value: "0" } });
  const panel = screen.getByRole("region", { name: "模板参数" });
  await waitFor(() => expect(within(panel).queryByText(/关键词样式由云端模板按摆放设置/) === null).toBe(true));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存配置" }).disabled).toBe(true);
});

// 旧版本的关键词默认值未关闭时，保存资产会被拒；用户可以在聊天里让 Agent 生成关键词默认关闭的新版本（保留外观）。
test("保存资产被关键词默认值拒绝时可以请 Agent 修复", async () => {
  const instructions: string[] = [];
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2], { keywordSize: 80 });
      version.spec.schema_version = "2";
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywordSize", title: "关键词字号" }] };
      version.candidate.config_schema.properties.keywordSize = { type: "integer", minimum: 0, maximum: 300, title: "关键词字号" };
      return Response.json(version);
    }
    if (path === "/api/sprites/publish") {
      return Response.json({ detail: "关键词参数的默认值必须是关闭状态：keywordSize（应为 0）。关键词样式由模板按摆放设置" }, { status: 422 });
    }
    if (path === "/works/work-1/messages") {
      instructions.push(JSON.parse(String(options?.body)).instruction ?? "");
      return Response.json(remotionJob("running"));
    }
  });
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await ready();
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存资产" });
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(save);
  await screen.findByText(/关键词参数的默认值必须是关闭状态/);
  fireEvent.click(screen.getByRole("button", { name: "让 Agent 修复" }));
  await waitFor(() => expect(instructions).toHaveLength(1));
  expect(instructions[0]).toContain("关键词参数的默认值恢复为关闭");
  expect(instructions[0]).toContain("保持现有外观");
});

// 旧版本的关键词默认值未关闭时，即使只修改其他参数也必须先恢复关闭；仅看补丁会漏掉这个不可发布的完整默认值。
test("旧版本仅修改其他参数时仍要求恢复关键词默认值", async () => {
  const writes: string[] = [];
  await history((path, options) => {
    if (/^\/versions\/version-\d+$/.test(path)) {
      const version = remotionVersion(path.split("/")[2], { keywordSize: 64 });
      version.spec.schema_version = "2";
      version.spec.sprite = { kind: "text", bindings: [{ name: "text", title: "文字" }, { name: "keywordSize", title: "关键词字号" }] };
      version.candidate.config_schema.properties.keywordSize = { type: "integer", minimum: 0, maximum: 300, title: "关键词字号" };
      return Response.json(version);
    }
    if (path === "/works/work-1/messages") {
      writes.push(String(options?.body ?? ""));
      return Response.json(remotionJob("running"));
    }
  });
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  act(() => result.current.change("size", 88));
  act(() => result.current.saveParameters());
  expect(result.current.error).toContain("keywordSize 应为 0");
  expect(writes).toHaveLength(0);
});

// 每次成功关联各自聊天结果；初始折叠，旧版本复制不能读成当前代码，查看版本不写服务端。
test("成功卡片默认折叠，复制对应版本并切换只读预览", async () => {
  await history((path) =>
    path.endsWith("/artifacts/Export.tsx")
      ? new Response(`export default '${path.split("/")[2]}';`)
      : undefined,
  );
  const copy = spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V1" });
  await screen.findByRole("button", { name: "预览 V2" });
  expect(screen.queryByLabelText("模板 TSX 代码")).toBeNull();
  const first = within(screen.getByLabelText("成功版本 V1"));
  fireEvent.click(first.getByRole("button", { name: "复制代码" }));
  await waitFor(() =>
    expect(copy).toHaveBeenCalledWith("export default 'version-1';"),
  );
  expect(screen.queryByLabelText("模板 TSX 代码")).toBeNull();
  fireEvent.click(first.getByRole("button", { name: "展开 V1 代码" }));
  expect(first.getByLabelText("模板 TSX 代码").textContent).toContain(
    "version-1",
  );
  fireEvent.click(first.getByRole("button", { name: "预览 V1" }));
  await waitFor(() =>
    expect(
      screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
    ).toContain("version-1"),
  );
  await ready();
  expect(screen.getByText("历史版本 · 只读")).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("字号").disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "保存配置" })).toBeNull();
  fireEvent.change(screen.getByLabelText("字效描述"), {
    target: { value: "保留输入" },
  });
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "返回最新版本" }));
  await waitFor(() =>
    expect(
      screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器").src,
    ).toContain("version-2"),
  );
  await ready();
  expect(screen.getByLabelText<HTMLInputElement>("字号").disabled).toBe(false);
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "发送" }).disabled,
  ).toBe(false);
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST" && !String(call[0]).endsWith("/ack")),
  ).toHaveLength(1);
});

// 目录异常独立于最新成功版本；只读重试可恢复旧版本，参数修订以单独来源显示。
test("目录失败不隐藏当前结果，重试恢复参数版本", async () => {
  let fail = true;
  await history((path) =>
    path.endsWith("/versions")
      ? fail
        ? new Response(null, { status: 503 })
        : Response.json([
            { ...remotionVersion(), source: "user_parameters" },
            remotionVersion("version-2"),
          ])
      : undefined,
  );
  render(<RemotionWorkspace />);
  await screen.findByRole("button", { name: "预览 V2" });
  await screen.findByText("历史版本读取失败，请重试。");
  expect(screen.queryByRole("button", { name: "预览 V1" })).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "重试历史版本" }));
  await screen.findByRole("button", { name: "预览 V1" });
  expect(screen.getByText(/参数调整 ·/)).toBeTruthy();
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST" && !String(call[0]).endsWith("/ack")),
  ).toHaveLength(1);
});

// 导出只按需读取，失败允许重试；剪贴板不可用时展示该版本代码供手动复制。
test("代码读取重试与剪贴板失败恢复", async () => {
  let fail = true;
  remotionServer((path) =>
    path.endsWith("/artifacts/Export.tsx")
      ? fail
        ? new Response(null, { status: 404 })
        : new Response("export default 'old';")
      : undefined,
  );
  const copy = spyOn(navigator.clipboard, "writeText").mockRejectedValue(
    new Error("denied"),
  );
  render(
    <VersionCard
      version={remotionVersion()}
      selected={false}
      latest={false}
      disabled={false}
      previewDisabled={false}
      onPreview={() => {}}
    />,
  );
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  await screen.findByText("代码读取失败，请重试。");
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "重试代码" }));
  await waitFor(() =>
    expect(codeText()).toBe("export default 'old';"),
  );
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
  await screen.findByText("复制失败，请展开代码后手动选择复制。");
  expect(codeText()).toBe("export default 'old';");
  expect(copy).toHaveBeenCalledTimes(1);
  // 只统计代码读取：展开时的诊断请求与复制去重无关。
  expect(
    fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith("/artifacts/Export.tsx"),
    ),
  ).toHaveLength(2);
});

// 复制请求尚未完成就卸载或进入未保存状态，迟到代码不得再写剪贴板。
test.each(["unmount", "disabled"])(
  "迟到复制受当前状态保护：%s",
  async (mode) => {
    let finish!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    remotionServer((_path, options) => {
      signal = options?.signal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    const copy = spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const props = {
      version: remotionVersion(),
      selected: false,
      latest: false,
      disabled: false,
      previewDisabled: false,
      onPreview: () => {},
    };
    const view = render(<VersionCard {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
    // 请求先读取本机设备 ID，等到真正发出后再改变状态。
    await waitFor(() => expect(finish).toBeDefined());
    if (mode === "unmount") {
      view.unmount();
      expect(signal?.aborted).toBe(true);
    } else view.rerender(<VersionCard {...props} disabled />);
    await act(async () => finish(new Response("export default 'late';")));
    expect(copy).not.toHaveBeenCalled();
  },
);

// 历史预览不能覆盖当前基线，新增成功事件也不能抢走明确选中的旧画面。
test("SSE 推进最新版本时保留历史预览，返回后发送绑定最新基线", async () => {
  const fake = await history((path) =>
    path.endsWith("/messages")
      ? Response.json({
          ...remotionJob("answered"),
          id: "answer-4",
          message: "已回答",
        })
      : undefined,
  );
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  act(() => result.current.selectVersion("version-1"));
  await waitFor(() =>
    expect(result.current.previewVersion?.id).toBe("version-1"),
  );
  act(() => {
    result.current.change("size", 99);
    result.current.send("历史不可提交");
  });
  expect(result.current.values.size).toBe(64);
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST" && !String(call[0]).endsWith("/ack")),
  ).toHaveLength(1);
  await act(async () =>
    fake.advance({ ...remotionJob("succeeded", "version-3"), id: "job-3" }),
  );
  await waitFor(() => expect(result.current.version?.id).toBe("version-3"));
  expect(result.current.previewVersion?.id).toBe("version-1");
  act(() => result.current.selectVersion("version-3"));
  expect(result.current.previewVersion).toBeNull();
  act(() => result.current.send("继续修改"));
  await waitFor(() => expect(result.current.job?.status).toBe("answered"));
  const post = fetchMock.mock.calls.find((call) =>
    String(call[0]).endsWith("/messages"),
  )!;
  expect(JSON.parse(String(post[1]?.body)).base_version_id).toBe("version-3");
});

// 未保存切换有取消、放弃两种不写入路径；取消保留草稿，放弃后仅更换只读预览。
test("历史预览沿用未保存参数的取消和放弃保护", async () => {
  await history();
  const { result } = renderHook(() => useTemplateSession(() => {}));
  await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
  act(() => {
    result.current.change("size", 80);
    result.current.selectVersion("version-1");
  });
  expect(result.current.navigation).toEqual({
    version: "version-1",
    saving: false,
  });
  act(() => result.current.resolveNavigation("cancel"));
  expect(result.current.previewVersion).toBeNull();
  expect(result.current.values.size).toBe(80);
  act(() => result.current.selectVersion("version-1"));
  act(() => result.current.resolveNavigation("discard"));
  await waitFor(() =>
    expect(result.current.previewVersion?.id).toBe("version-1"),
  );
  expect(result.current.version?.id).toBe("version-2");
  expect(result.current.dirty).toBe(false);
  expect(result.current.values.size).toBe(64);
  expect(
    fetchMock.mock.calls.filter((call) => call[1]?.method === "POST" && !String(call[0]).endsWith("/ack")),
  ).toHaveLength(1);
});

// 保存成功且产物读回后才进入旧版本；保存失败保留原草稿和目标，允许取消继续编辑。
test.each(["succeeded", "failed"] as const)(
  "保存后预览旧版本：%s",
  async (status) => {
    await history((path) =>
      path.endsWith("/messages")
        ? Response.json({ ...remotionJob(status, "version-3"), id: "save-3" })
        : undefined,
    );
    const { result } = renderHook(() => useTemplateSession(() => {}));
    await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
    act(() => {
      result.current.change("size", 80);
      result.current.selectVersion("version-1");
    });
    act(() => result.current.resolveNavigation("save"));
    await waitFor(() => expect(result.current.job?.id).toBe("save-3"));
    await waitFor(() => expect(result.current.busy).toBeNull());
    if (status === "succeeded") {
      await waitFor(() =>
        expect(result.current.previewVersion?.id).toBe("version-1"),
      );
      expect(result.current.version?.id).toBe("version-3");
      expect(result.current.navigation).toBeNull();
    } else {
      expect(result.current.previewVersion).toBeNull();
      expect(result.current.values.size).toBe(80);
      expect(result.current.navigation?.saving).toBe(false);
      expect(result.current.error).toContain("未能完成");
    }
    const post = fetchMock.mock.calls.find((call) =>
      String(call[0]).endsWith("/messages"),
    )!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({
      base_version_id: "version-2",
      parameters: { size: 80 },
    });
  },
);

// 多个异步预览读取可能乱序；返回最新、切换会话和卸载均取消读取，旧响应无权回填。
test.each(["latest", "work", "unmount"])(
  "迟到版本读取不能覆盖新选择：%s",
  async (target) => {
    let finish!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    await history((path, options) =>
      path === "/versions/version-1"
        ? new Promise((resolve) => {
            signal = options?.signal;
            finish = resolve;
          })
        : undefined,
    );
    const { result, unmount } = renderHook(() => useTemplateSession(() => {}));
    await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
    act(() => result.current.selectVersion("version-1"));
    expect(result.current.previewLoading).toBe(true);
    await waitFor(() => expect(finish).toBeDefined());
    if (target === "unmount") unmount();
    else
      act(() =>
        target === "latest"
          ? result.current.selectVersion("version-2")
          : result.current.reset(),
      );
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(Response.json(remotionVersion())));
    expect(result.current.previewVersion).toBeNull();
    if (target !== "unmount") expect(result.current.previewLoading).toBe(false);
  },
);

// 读取错误或返回另一会话的数据不替换可用结果；返回最新清除错误，重选可以显式重试。
test.each(["http", "owner", "id"])(
  "历史读取失败保留当前结果并可恢复：%s",
  async (kind) => {
    let fail = true;
    await history((path) =>
      path === "/versions/version-1" && fail
        ? kind === "http"
          ? new Response(null, { status: 503 })
          : Response.json({
              ...remotionVersion(),
              ...(kind === "owner" ? { project_id: "other" } : { id: "other" }),
            })
        : undefined,
    );
    const { result } = renderHook(() => useTemplateSession(() => {}));
    await waitFor(() => expect(result.current.version?.id).toBe("version-2"));
    act(() => result.current.selectVersion("version-1"));
    await waitFor(() => expect(result.current.previewError).not.toBe(""));
    expect(result.current.previewVersion).toBeNull();
    expect(result.current.version?.id).toBe("version-2");
    act(() => result.current.selectVersion("version-2"));
    expect(result.current.previewError).toBe("");
    fail = false;
    act(() => result.current.selectVersion("version-1"));
    await waitFor(() =>
      expect(result.current.previewVersion?.id).toBe("version-1"),
    );
  },
);

// 目录跨会话响应同样可能迟到，不得泄露旧会话代码或版本卡片；卸载取消剩余读取。
test("版本目录隔离会话并清理请求", async () => {
  let finish!: (response: Response) => void;
  const signals: AbortSignal[] = [];
  remotionServer((path, options) => {
    signals.push(options!.signal!);
    return path.includes("work-1")
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : new Promise<Response>(() => {});
  });
  const { result, rerender, unmount } = renderHook(
    ({ work }) => useTemplateVersions(work, null),
    { initialProps: { work: "work-1" } },
  );
  await waitFor(() => expect(signals).toHaveLength(1));
  rerender({ work: "work-2" });
  await waitFor(() => expect(signals).toHaveLength(2));
  await act(async () => finish(Response.json([remotionVersion()])));
  expect(signals[0].aborted).toBe(true);
  expect(result.current.versions).toEqual([]);
  unmount();
  expect(signals[1].aborted).toBe(true);
});
