/** 主页云端模板列表、摘要与删除确认测试；真实组件调用存储边界，隔离 HTTP 与桌面 IPC。 */
import { expect, test } from "bun:test";
import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Tooltip } from "radix-ui";
import { TemplateCollection, TemplateHome, type TemplateSelection } from "@/features/templates/TemplateHome";
import { jsonListResponse, savedTemplate, templateSummary } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 场景：拖动后的合成点击不干扰当前位置；随后点侧面封面时只居中。
test("空间画廊拖动换页不会误打开模板", () => {
  const templates = Array.from({ length: 3 }, (_, index) => ({ ...templateSummary(savedTemplate()), template_id: `drag-${index}`, name: `拖动 ${index}` }));
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection collection={{ templates, loading: false, error: "", refresh: () => {} }} onCreate={() => {}} onSelect={(value) => selections.push(value)} />);
  const stage = screen.getByLabelText("拖动或滚轮浏览模板");
  Object.defineProperty(stage.querySelector(".template-gallery-item"), "offsetWidth", { value: 240 });
  stage.setPointerCapture = () => {};
  fireEvent.pointerDown(stage, { button: 0, pointerId: 1, clientX: 500 });
  fireEvent.pointerMove(stage, { pointerId: 1, clientX: 300 });
  expect(stage.getAttribute("data-dragging")).toBe("true");
  fireEvent.pointerUp(stage, { pointerId: 1, clientX: 300 });
  expect(screen.getByRole("heading", { name: "拖动 1", level: 2 })).toBeTruthy();
  const card = within(stage).getByRole("button", { name: "居中展示：拖动 2" });
  fireEvent.click(card, { detail: 1 });
  expect(screen.getByRole("heading", { name: "拖动 1", level: 2 })).toBeTruthy();
  fireEvent.pointerDown(card, { button: 0, pointerId: 2, clientX: 300 });
  fireEvent.pointerUp(card, { pointerId: 2, clientX: 300 });
  fireEvent.click(card, { detail: 1 });
  expect(screen.getByRole("heading", { name: "拖动 2", level: 2 })).toBeTruthy();
  expect(selections).toHaveLength(0);
});

// 场景：滚轮可双向循环浏览；Ctrl 缩放与单张封面的默认滚动不被接管。
test("空间画廊滚轮浏览仅接管多模板区域", () => {
  const templates = Array.from({ length: 3 }, (_, index) => ({ ...templateSummary(savedTemplate()), template_id: `wheel-${index}`, name: `滚轮 ${index}` }));
  const props = { onCreate: () => {}, onSelect: () => {} };
  const collection = { templates, loading: false, error: "", refresh: () => {} };
  const view = render(<TemplateCollection {...props} collection={collection} />);
  const stage = screen.getByLabelText("拖动或滚轮浏览模板");
  expect(fireEvent.wheel(stage, { deltaY: -100 })).toBe(false);
  expect(screen.getByRole("heading", { name: "滚轮 2", level: 2 })).toBeTruthy();
  fireEvent.wheel(stage, { deltaY: 100 });
  expect(screen.getByRole("heading", { name: "滚轮 0", level: 2 })).toBeTruthy();
  const zoom = createEvent.wheel(stage, { deltaY: 100 });
  // Happy DOM 的 WheelEvent 尚未继承鼠标修饰键，补齐真实浏览器会提供的只读属性。
  Object.defineProperty(zoom, "ctrlKey", { value: true });
  expect(fireEvent(stage, zoom)).toBe(true);
  view.rerender(<TemplateCollection {...props} collection={{ ...collection, templates: templates.slice(0, 1) }} />);
  expect(fireEvent.wheel(stage, { deltaY: 100 })).toBe(true);
});

// 场景：空间陈列只保留最近五项，全部模板仍可打开；删除当前陈列项后回到有效记录。
test("最近五项与全部模板共用打开和删除状态", () => {
  const templates = Array.from({ length: 7 }, (_, index) => ({ ...templateSummary(savedTemplate()), template_id: `template-${index}`, name: `模板 ${index}`, updated_at: `2026-11-0${7-index}T00:00:00Z` }));
  const selections: TemplateSelection[] = [];
  const props = { onCreate: () => {}, onSelect: (value: TemplateSelection) => selections.push(value) };
  const collection = { templates: [...templates].reverse(), loading: false, error: "", refresh: () => {} };
  const view = render(<TemplateCollection {...props} collection={collection} />);
  const recent = within(screen.getByRole("region", { name: "最近修改的模板" }));
  const all = within(screen.getByRole("region", { name: "全部模板" }));
  expect(recent.getAllByRole("button", { name: /^居中展示：/ })).toHaveLength(5);
  expect(all.queryByRole("button", { name: "打开编辑" })).toBeNull();
  expect(recent.queryByRole("button", { name: "浏览模板：模板 6" })).toBeNull();
  expect(screen.queryByRole("button", { name: /继续编辑/ })).toBeNull();
  fireEvent.click(all.getByRole("button", { name: "选择模板：模板 6" }));
  fireEvent.keyDown(recent.getByRole("button", { name: "居中展示：模板 0" }), { key: "ArrowLeft" });
  expect(recent.getByRole("heading", { name: "模板 4" })).toBeTruthy();
  const centeredCard = recent.getByRole("button", { name: "居中展示：模板 4" });
  fireEvent.click(centeredCard);
  expect(selections).toEqual([{ templateId: "template-6" }]);
  fireEvent.click(within(centeredCard.closest("article")!).getByRole("button", { name: "打开编辑" }));
  expect(selections).toEqual([{ templateId: "template-6" }, { templateId: "template-4" }]);
  view.rerender(<TemplateCollection {...props} collection={{ ...collection, templates: templates.filter((item) => item.template_id !== "template-4") }} />);
  expect(recent.getByRole("heading", { name: "模板 0" })).toBeTruthy();
});

// 场景：全部模板连续展示；名称搜索忽略大小写和首尾空格，无结果时明确提示。
test("模板网格连续展示并按名称搜索", async () => {
  const templates = Array.from({ length: 32 }, (_, index) => ({ ...savedTemplate(), template_id: `page-${index}`, name: `Travel ${index}`, updated_at: new Date(Date.UTC(2026, 0, index + 1)).toISOString() }));
  fetchMock.mockImplementation((async (_url) => jsonListResponse(templates)) as typeof fetch);
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  const grid = within(await screen.findByRole("region", { name: "全部模板" }));
  expect(grid.getAllByRole("button", { name: /^选择模板：/ })).toHaveLength(32);
  expect(grid.getAllByRole("heading")[1].textContent).toBe("Travel 31");
  expect(grid.getByRole("status").textContent).toBe("共 32 个模板");
  expect(screen.queryByRole("navigation", { name: "模板分页" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索模板名称" }), { target: { value: "  tRaVeL 3  " } });
  expect(grid.getAllByRole("button", { name: /^选择模板：/ })).toHaveLength(3);
  expect(grid.getByRole("status").textContent).toBe("共 3 个模板");
  expect(screen.queryByRole("region", { name: "最近修改的模板" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索模板名称" }), { target: { value: "不存在" } });
  expect(grid.queryAllByRole("button", { name: /^选择模板：/ })).toHaveLength(0);
  expect(grid.getByText(/没有找到匹配的模板/)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

// 场景：删除末项后其余模板仍连续展示，数量同步更新。
test("删除末项后网格连续展示剩余模板", () => {
  const templates = Array.from({ length: 16 }, (_, index) => ({ ...templateSummary(savedTemplate()), template_id: `page-${index}`, name: `模板 ${index}` }));
  const collection = { templates, loading: false, error: "", refresh: () => {} };
  const props = { onCreate: () => {}, onSelect: () => {} };
  const view = render(<TemplateCollection {...props} collection={collection} />);
  view.rerender(<TemplateCollection {...props} collection={{ ...collection, templates: templates.slice(0, -1) }} />);
  const grid = within(screen.getByRole("region", { name: "全部模板" }));
  expect(grid.getAllByRole("button", { name: /^选择模板：/ })).toHaveLength(15);
  expect(grid.getByRole("status").textContent).toBe("共 15 个模板");
});

// 场景：无效模板只可进入删除确认，取消后仍可选择同库有效模板。
test("无效记录显示原因且不能打开，删除确认保留有效模板", () => {
  const valid = templateSummary(savedTemplate());
  const invalid = { ...valid, template_id: "invalid-template", name: "无效模板", validation_error: "转场必须指定视频交界处", track_count: undefined };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection collection={{ templates: [valid, invalid], loading: false, error: "", refresh: () => {} }} onCreate={() => {}}
    onSelect={(selection) => selections.push(selection)} />);
  expect(within(screen.getByRole("region", { name: "全部模板" })).getByRole<HTMLButtonElement>("button", { name: "选择模板：无效模板" }).disabled).toBe(true);
  expect(within(screen.getByRole("region", { name: "全部模板" })).getByText(invalid.validation_error)).toBeTruthy();
  fireEvent.click(within(screen.getByRole("region", { name: "全部模板" })).getByRole("button", { name: "删除模板：无效模板" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain(invalid.template_id);
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.click(within(screen.getByRole("region", { name: "全部模板" })).getByRole("button", { name: `选择模板：${valid.name}` }));
  expect(selections).toEqual([{ templateId: valid.template_id }]);
});

// 场景：有效模板显示 UUID，取消不删除，确认失败可重试，成功后刷新列表。
test("模板确认删除且失败保留重试入口", async () => {
  const template = templateSummary(savedTemplate());
  const removed: TemplateSelection[] = [];
  const requests: unknown[] = [];
  let refreshed = 0;
  let fail = true;
  fetchMock.mockImplementation((async (url, init) => {
    requests.push({ url: String(url), method: init?.method });
    return fail ? new Response(JSON.stringify({ detail: "测试删除失败" }), { status: 503 }) : new Response(null, { status: 204 });
  }) as typeof fetch);
  render(<TemplateCollection collection={{ templates: [template], loading: false, error: "", refresh: () => refreshed++ }} onCreate={() => {}}
    onSelect={() => { throw new Error("删除不能打开模板"); }} onDelete={(selection) => removed.push(selection)} />);
  fireEvent.click(within(screen.getByRole("region", { name: "全部模板" })).getByRole("button", { name: `删除模板：${template.name}` }));
  expect(screen.getByRole("dialog").textContent).toContain(template.name);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(requests).toHaveLength(0);
  fireEvent.click(within(screen.getByRole("region", { name: "全部模板" })).getByRole("button", { name: `删除模板：${template.name}` }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByText(/测试删除失败/);
  expect(refreshed).toBe(0);
  expect(removed).toHaveLength(0);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "重试删除" }));
  await waitFor(() => expect(removed).toHaveLength(1));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(refreshed).toBe(1);
  expect(removed).toEqual([{ templateId: template.template_id }]);
  expect(requests).toEqual([1, 2].map(() => ({ url: `http://api.test:8000/template/${template.template_id}`, method: "DELETE" })));
});

// 场景：桌面客户端主页只列出和新建云端模板，没有本地模板的入口、页签或 IPC 读取。
test("主页只提供云端模板", async () => {
  const ipc: string[] = [];
  mockDesktop(async (command) => { ipc.push(command); throw new Error("意外 IPC 请求"); });
  fetchMock.mockImplementation((async (_url, _init) => jsonListResponse([{ ...savedTemplate(), name: "共享模板" }])) as typeof fetch);
  const selections: TemplateSelection[] = [];
  render(<Tooltip.Provider><TemplateHome onSelect={(value) => selections.push(value)} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("region", { name: "全部模板" });
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(screen.queryByRole("button", { name: "新建本地模板" })).toBeNull();
  expect(screen.queryAllByRole("tab")).toHaveLength(0);
  expect(screen.queryByText(/本地模板/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "新建云端模板" }));
  const dialog = screen.getByRole("dialog", { name: "新建云端模板" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "模板名称" }), { target: { value: "  新模板  " } });
  fireEvent.click(within(dialog).getByRole("button", { name: "进入编辑" }));
  expect(selections).toEqual([{ templateId: null, name: "新模板", description: "" }]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(ipc).toHaveLength(0);
});

// 场景：云端读取失败显示原因与重试入口，重试成功后显示模板。
test("云端读取失败可重试", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "云端暂不可用" }), { status: 503 }));
  fetchMock.mockResolvedValueOnce(jsonListResponse([savedTemplate()]));
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByText("云端模板读取失败");
  expect(screen.getByText("云端暂不可用")).toBeTruthy();
  expect(screen.getByLabelText("模板数量").textContent).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  await screen.findByRole("region", { name: "全部模板" });
  expect(screen.getByLabelText("模板数量").textContent).toBe("1");
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

// 手动同步保留当前网格与选中的封面；缺图只重传已有截图，健康且版本未变化的封面不再请求。
test("主页刷新只修复待发布封面，保留陈列位置且不进入编辑", async () => {
  const { publishCover } = await import("@/features/templates/api");
  const old = { url: "https://images.test/library.png", revision: "a", publishedRevision: "a", source: "builtin" };
  const healthy = { ...savedTemplate(), template_id: "home-healthy", cover: old };
  const pending = { ...healthy, name: "缺图", template_id: "home-pending", cover: { ...old, url: "", revision: "b", publishedRevision: "" } };
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
  await expect(publishCover(pending.template_id, "b", async () => new Blob(["one-second"]))).rejects.toThrow();
  const calls: string[] = [];
  let finish!: (response: Response) => void;
  fetchMock.mockImplementation((async (input, init) => {
    const path = new URL(String(input)).pathname;
    calls.push((init?.method ?? "GET") + " " + path);
    if (path === "/template") return jsonListResponse([healthy, pending]);
    if (init?.method === "PUT") return new Promise<Response>(resolve => { finish = resolve; });
    if (path.endsWith("/cover")) return new Response("PNG");
    throw new Error(path);
  }) as typeof fetch);
  const selections: TemplateSelection[] = [];
  render(<Tooltip.Provider><TemplateHome onSelect={selection => selections.push(selection)} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  const recent = within(await screen.findByRole("region", { name: "最近修改的模板" }));
  fireEvent.click(await recent.findByRole("button", { name: "居中展示：缺图" }));
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "刷新模板及封面" });
  fireEvent.click(button);
  await waitFor(() => expect(finish).toBeDefined());
  expect(button.disabled).toBe(true);
  expect(button.querySelector(".animate-spin")).toBeTruthy();
  expect(recent.getByRole("heading", { name: "缺图" })).toBeTruthy();
  finish(Response.json({ revision: "b", url: "https://images.test/pending.png" }));
  await waitFor(() => expect(button.disabled).toBe(false));
  await waitFor(() => expect(screen.queryByText("封面更新失败")).toBeNull());
  expect(calls.filter(call => call === "GET /template")).toHaveLength(2);
  expect(calls.filter(call => call.startsWith("PUT"))).toEqual(["PUT /template/home-pending/cover/b"]);
  expect(calls.filter(call => call.endsWith("/home-healthy/cover"))).toHaveLength(1);
  expect(calls.filter(call => call.endsWith("/home-pending/cover"))).toHaveLength(1);
  expect(selections).toHaveLength(0);
});

// 断网时两个陈列区都回退到直链；服务恢复后，手动刷新应共享一次重读并原地换图。
test("主页刷新重读上次失败的同版封面，恢复后继续命中缓存", async () => {
  const cover = { url: "https://images.test/offline.png", revision: "v1", publishedRevision: "v1", source: "builtin" };
  const item = { ...savedTemplate(), template_id: "offline-cover", cover };
  let lists = 0, reads = 0;
  let online = false;
  fetchMock.mockImplementation((async (input, init) => {
    if (init?.method === "GET" && String(input).endsWith("/template")) {
      lists++;
      return jsonListResponse([item]);
    }
    if (init?.method === "GET" && String(input).endsWith("/offline-cover/cover")) {
      reads++;
      return online ? new Response("PNG") : new Response(null, { status: 503 });
    }
    throw new Error("意外的请求：" + input);
  }) as typeof fetch);
  const view = render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  const images = () => [...view.container.querySelectorAll<HTMLImageElement>("article img")].map(img => img.src);
  await waitFor(() => expect(images()).toEqual([cover.url, cover.url]));
  const refresh = screen.getByRole<HTMLButtonElement>("button", { name: "刷新模板及封面" });
  fireEvent.click(refresh);
  await waitFor(() => expect([lists, reads, refresh.disabled]).toEqual([2, 2, false]));
  online = true;
  fireEvent.click(refresh);
  await waitFor(() => expect(images().filter(src => src.startsWith("blob:"))).toHaveLength(2));
  fireEvent.click(refresh);
  await waitFor(() => expect([lists, reads, refresh.disabled]).toEqual([4, 3, false]));
});

// 同版对象丢失时强制写回手头的 PNG，两处陈列共用修复结果，下次刷新继续命中缓存。
test("主页修复数据库已发布但 ZOS 对象缺失的封面", async () => {
  const { publishCover } = await import("@/features/templates/api");
  const cover = { url: "https://images.test/lost.png", revision: "v1", publishedRevision: "v1", source: "builtin" };
  const item = { ...savedTemplate(), template_id: "lost-object", cover };
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
  await expect(publishCover(item.template_id, "v1", async () => new Blob(["saved PNG"]))).rejects.toThrow();
  const writes: FormData[] = [];
  let reads = 0, lists = 0;
  fetchMock.mockImplementation((async (input, init) => {
    if (String(input).endsWith("/template")) {
      lists++;
      return jsonListResponse([item]);
    }
    if (init?.method === "PUT") {
      writes.push(init.body as FormData);
      cover.revision = cover.publishedRevision = "v2";
      return Response.json(cover);
    }
    reads++;
    return writes.length ? new Response("restored PNG") : Response.json({ detail: { code: "cover_object_missing" } }, { status: 404 });
  }) as typeof fetch);
  const view = render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  const sources = () => Array.from(view.container.querySelectorAll("article img"), image => image.getAttribute("src"));
  await waitFor(() => expect(sources()).toEqual([cover.url, cover.url]));
  const refresh = screen.getByRole<HTMLButtonElement>("button", { name: "刷新模板及封面" });
  fireEvent.click(refresh);
  await waitFor(() => expect(sources().filter(src => src?.startsWith("blob:"))).toHaveLength(2));
  expect(writes[0].get("refresh")).toBe("true");
  expect(await (writes[0].get("file") as File).text()).toBe("saved PNG");
  fireEvent.click(refresh);
  await waitFor(() => expect([lists, reads, writes.length, refresh.disabled]).toEqual([3, 3, 1, false]));
});

// 列表带回已发布的新版本时，旧图在下载期间继续展示，两处卡片共享一次读取。
test("同步已发布的新版封面时等图片读完再停止旋转", async () => {
  const item = { ...savedTemplate(), template_id: "home-published" };
  let reads = 0, lists = 0;
  let downloaded!: (image: Response) => void;
  fetchMock.mockImplementation((async input => {
    if (String(input).endsWith("/template")) {
      const revision = String(++lists);
      return jsonListResponse([{ ...item, cover: { url: "https://images.test/stable.png", revision, publishedRevision: revision, source: "builtin" } }]);
    }
    if (String(input).endsWith("/cover")) {
      if (++reads === 1) return new Response("old");
      return new Promise<Response>(resolve => { downloaded = resolve; });
    }
    throw new Error(String(input));
  }) as typeof fetch);
  const view = render(<Tooltip.Provider><TemplateHome onSelect={() => { throw new Error("不应进入编辑页"); }} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await waitFor(() => expect(view.container.querySelectorAll("article img")).toHaveLength(2));
  const before = view.container.querySelector("article img")!.getAttribute("src");
  const button = screen.getByRole<HTMLButtonElement>("button", { name: "刷新模板及封面" });
  fireEvent.click(button);
  await waitFor(() => expect(downloaded).toBeDefined());
  expect(button.disabled).toBe(true);
  expect(view.container.querySelector("article img")!.getAttribute("src")).toBe(before);
  downloaded(new Response("new"));
  await waitFor(() => expect(button.disabled).toBe(false));
  expect(view.container.querySelector("article img")!.getAttribute("src")).not.toBe(before);
  expect([lists, reads]).toEqual([2, 2]);
});

// 未授权浏览器截图时也能先重传已有图片，明确缺少原视频的模板保持失败状态。
test("浏览器只为真正需要补截的模板请求共享权限", async () => {
  const { publishCover } = await import("@/features/templates/api");
  const cover = { url: "", revision: "pending", publishedRevision: "", source: "builtin" };
  const input = { ...savedTemplate(), template_id: "browser-media", cover };
  const cached = { ...input, template_id: "browser-png", name: "已截取" };
  const unavailable = { ...input, template_id: "browser-unavailable", name: "无原视频", cover: { ...cover, source: "unavailable" } };
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
  await expect(publishCover(cached.template_id, cover.revision, async () => new Blob(["PNG"]))).rejects.toThrow();
  const calls: string[] = [];
  fetchMock.mockImplementation((async (inputUrl, init) => {
    const path = new URL(String(inputUrl)).pathname;
    calls.push(path);
    if (path === "/template") return jsonListResponse([input, unavailable, cached]);
    if (init?.method === "PUT") return Response.json({ url: "https://images.test/repaired.png", revision: cover.revision });
    if (path.endsWith("/cover")) return new Response("PNG");
    throw new Error(path);
  }) as typeof fetch);
  render(<Tooltip.Provider><TemplateHome onSelect={() => {}} onOpenRemotion={() => {}} /></Tooltip.Provider>);
  await screen.findByRole("region", { name: "全部模板" });
  fireEvent.click(screen.getByRole("button", { name: "刷新模板及封面" }));
  await screen.findByRole("button", { name: "共享当前标签页以补截封面" });
  expect(calls).toEqual(["/template", "/template", "/template/browser-png/cover/pending", "/template/browser-png/cover"]);
});
