/** 模板 HTTP 核心测试：请求契约、必要校验与错误展示；fetch 由 setup.ts 隔离。 */
import { expect, spyOn, test } from "bun:test";
import { fromBinary, toBinary, toJson } from "@bufbuild/protobuf";
import { EffectTemplateEditorSchema, GetTemplateResponseSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { deleteTemplate, getTemplate, listTemplates, saveTemplate } from "@/features/templates/api";
import { newDraft, toDraft, type RemotionClip } from "@/features/templates/model";
import { protobufListResponse, protobufTemplateResponse, savedTemplate, templateSummary } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 测试创建和更新都使用 POST /template，只有更新带 ID，名称说明被修剪且效果去重。
test.each([undefined, "existing-id"])("保存请求正确区分创建与更新：%s", async (id) => {
  const saved = savedTemplate();
  const draft = toDraft(saved);
  draft.name = "  我的模板  ";
  draft.description = "  说明  ";
  draft.tracks[1].editor.subtitleIn = "in/fade_in";
  draft.tracks[0].editor.titleKeywordSize = 72;
  draft.tracks[1].editor.subtitleKeywordSize = 48;
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "save", id ? 200 : 201));
  expect(await saveTemplate(draft, id)).toEqual(saved);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api.test:8000/template");
  expect(options?.method).toBe("POST");
  expect(options?.headers).toEqual({ Accept: "application/x-protobuf", "Content-Type": "application/x-protobuf" });
  const body = fromBinary(SaveTemplateRequestSchema, new Uint8Array(options?.body as Uint8Array));
  expect(body).toMatchObject({ name: "我的模板", description: "说明", effectIds: ["in/fade_in"] });
  expect(body.tracks?.tracks.map((track) => [track.editor?.titleKeywordSize, track.editor?.subtitleKeywordSize]))
    .toEqual([[72, 0], [0, 48]]);
  if (id) expect(body.templateId).toBe(id);
  else expect(body.templateId).toBeUndefined();
  expect(body).not.toHaveProperty("effects");
  expect(body).not.toHaveProperty("editor");
  expect(draft.name).toBe("  我的模板  ");
});

// 测试名称为空或缺少全部对象时直接提示，不发送无效请求。
test("保存前检查名称和画面对象", async () => {
  const draft = newDraft();
  await expect(saveTemplate(draft)).rejects.toThrow("请输入模板名称");
  draft.name = "新模板";
  draft.tracks = [];
  await expect(saveTemplate(draft)).rejects.toThrow("保存模板需要标题和底部字幕各一个");
  expect(fetchMock).not.toHaveBeenCalled();
});

// 测试创建与更新都要求标题和底部字幕各一个，缺少时不发送请求。
test("创建和更新均拒绝缺少固定文字对象", async () => {
  for (const id of [undefined, "existing-id"])
    for (const missing of [["title"], ["subtitle"], ["title", "subtitle"]]) {
      const draft = { ...newDraft(), name: "数量验证" };
      draft.tracks = draft.tracks.filter((track) => !missing.includes(track.target));
      await expect(saveTemplate(draft, id)).rejects.toThrow("保存模板需要标题和底部字幕各一个");
    }
  expect(fetchMock).not.toHaveBeenCalled();
});

// 测试列表与详情响应透传、路径 ID 编码，以及删除成功的 204 不尝试解析 JSON。
test("读取与删除遵循接口契约", async () => {
  const saved = savedTemplate();
  saved.tracks[0].editor.titleKeywordSize = 72;
  saved.tracks[1].editor.subtitleKeywordSize = 48;
  fetchMock.mockResolvedValueOnce(protobufListResponse([saved]));
  expect(await listTemplates()).toEqual([templateSummary(saved)]);
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "get"));
  expect(await getTemplate("id/with space")).toEqual(saved);
  expect(fetchMock.mock.calls[1][0]).toBe("http://api.test:8000/template/id%2Fwith%20space");
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  expect(await deleteTemplate(saved.template_id)).toBeUndefined();
  expect(fetchMock.mock.calls[2][0]).toBe(`http://api.test:8000/template/${saved.template_id}`);
  expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
});

const spriteId = "6f1c8a52-3d94-4b7e-9a10-2c5e8d7b4f31";
const clips: RemotionClip[] = [
  { id: "clip-a", sprite_id: spriteId, start: 2.5, duration: 3, parameters: {}, transform: {}, text_target: "title" },
  { id: "clip-b", sprite_id: spriteId, start: 0, duration: null, parameters: {}, transform: {}, transition_boundary: 2 },
];

// 测试含 Remotion 片段的模板（片段轨道的 editor 为空消息）可以正常读取：片段与 IMS 对象分开，且不会拖垮整个列表。
test("读取含 Remotion 片段的模板", async () => {
  const withClips = { ...savedTemplate(), template_id: "00000000-0000-4000-8000-000000000002", clips };
  fetchMock.mockResolvedValueOnce(protobufListResponse([savedTemplate(), withClips]));
  const listed = await listTemplates();
  expect(listed).toEqual([templateSummary(savedTemplate()), templateSummary(withClips)]);
  expect(listed[1].track_count).toBe(4);
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(withClips, "get"));
  expect(await getTemplate(withClips.template_id)).toEqual(withClips);
});

// 测试保存时片段排在固定文字对象之后，片段带上引用、秒数起点、所占的文字对象或交界处和空 editor 消息。
test("保存 Remotion 片段", async () => {
  const draft = { ...newDraft(), name: "文字与片段", clips };
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse({ ...savedTemplate(), clips }, "save", 201));
  await saveTemplate(draft);
  const body = fromBinary(SaveTemplateRequestSchema, new Uint8Array(fetchMock.mock.calls[0][1]?.body as Uint8Array));
  expect(body.effectIds).toEqual([]);
  expect(body.tracks?.tracks.slice(2).map((track) => [track.id, track.target, track.startMode, track.start, track.duration, track.spriteId, track.textTarget, track.transitionBoundary])).toEqual([
    ["clip-a", "remotion", "seconds", 2.5, 3, spriteId, "title", undefined],
    ["clip-b", "remotion", "seconds", 0, undefined, spriteId, undefined, 2],
  ]);
  expect(body.tracks?.tracks.slice(2).map((track) => track.editor && toJson(EffectTemplateEditorSchema, track.editor))).toEqual([{}, {}]);
  const mixed = { ...toDraft(savedTemplate()), clips };
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse({ ...savedTemplate(), clips }, "save", 201));
  await saveTemplate(mixed);
  const sent = fromBinary(SaveTemplateRequestSchema, new Uint8Array(fetchMock.mock.calls[1][1]?.body as Uint8Array));
  expect(sent.tracks?.tracks.map((track) => track.target)).toEqual(["title", "subtitle", "remotion", "remotion"]);
  expect(sent.effectIds).toEqual(["in/fade_in"]);
});

// 测试读取后原样保存：编辑其他字段不会丢失或改写片段，也不会把它们当成 IMS 对象。
test("含片段的模板读取后再保存不丢片段", async () => {
  const withClips = { ...savedTemplate(), clips };
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(withClips, "get"));
  const draft = toDraft(await getTemplate(withClips.template_id));
  draft.name = "改名";
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(withClips, "save"));
  await saveTemplate(draft, withClips.template_id);
  const body = fromBinary(SaveTemplateRequestSchema, new Uint8Array(fetchMock.mock.calls[1][1]?.body as Uint8Array));
  const sent = body.tracks!.tracks.filter((track) => track.target === "remotion");
  expect(sent.map((track) => [track.id, track.spriteId, track.start, track.duration])).toEqual([
    ["clip-a", spriteId, 2.5, 3], ["clip-b", spriteId, 0, undefined],
  ]);
  expect(body.tracks!.tracks.length - sent.length).toBe(2);
});

// 测试服务端遗漏编辑参数时拒绝详情响应，避免客户端静默补齐字段。
test("模板响应缺少编辑参数时拒绝读取", async () => {
  const response = protobufTemplateResponse(savedTemplate(), "get");
  const message = fromBinary(GetTemplateResponseSchema, new Uint8Array(await response.arrayBuffer()));
  message.template!.tracks!.tracks[0].editor!.title = undefined;
  fetchMock.mockResolvedValueOnce(new Response(Uint8Array.from(toBinary(GetTemplateResponseSchema, message))));
  await expect(getTemplate("missing-editor-field")).rejects.toThrow("模板编辑配置缺少必要字段");
});

// 测试重名与字段校验错误显示服务端提示，断网转换为可理解的错误且不自动重试。
test("服务端与网络错误转换为用户提示", async () => {
  fetchMock.mockResolvedValueOnce(Response.json({ detail: "模板名称已存在" }, { status: 409 }));
  await expect(saveTemplate(toDraft(savedTemplate()))).rejects.toThrow("模板名称已存在");
  fetchMock.mockResolvedValueOnce(Response.json({ detail: [{ loc: ["body", "editor", "titleSize"], msg: "字号越界" }] }, { status: 422 }));
  await expect(saveTemplate(toDraft(savedTemplate()))).rejects.toThrow("editor.titleSize：字号越界");
  fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await expect(listTemplates()).rejects.toThrow("无法连接服务端");
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

// 测试各类 HTTP 错误原样显示服务端提示，服务不可用时也不再建议切换到已移除的本地模板。
test.each([400, 404, 409, 422, 500, 502, 503, 504])("按 HTTP 状态显示服务端提示：%s", async (status) => {
  fetchMock.mockResolvedValueOnce(Response.json({ detail: "服务端提示" }, { status }));
  await expect(listTemplates()).rejects.toThrow(new Error("服务端提示"));
});

// 测试网关返回空正文或普通文本时展示 HTTP 状态。
test.each([null, "upstream unavailable"])("非 JSON 的 503 响应显示 HTTP 状态：%s", async (body) => {
  fetchMock.mockResolvedValueOnce(new Response(body, { status: 503 }));
  await expect(listTemplates()).rejects.toThrow(new Error("HTTP 503"));
});

// 测试超时保留保存结果不确定的提醒，主动取消不误报服务不可用。
test.each([false, true])("超时与主动取消区分处理：%s", async (cancelled) => {
  const timer = spyOn(window, "setTimeout");
  const response = Promise.withResolvers<Response>();
  fetchMock.mockReturnValueOnce(response.promise);
  const controller = new AbortController();
  const pending = listTemplates(controller.signal);
  fetchMock.mock.calls[0][1]?.signal?.addEventListener("abort", () => response.reject(new DOMException("已取消", "AbortError")), { once: true });
  if (cancelled) controller.abort();
  else {
    const timeout = timer.mock.calls[0][0];
    expect(typeof timeout).toBe("function");
    if (typeof timeout === "function") timeout();
  }
  await expect(pending).rejects.toThrow(cancelled
    ? "已取消"
    : new Error("请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。"));
});

// 回归：桌面客户端同样只读写云端模板，不再调用本地模板 IPC。
test("桌面客户端模板操作只走云端 HTTP", async () => {
  const { mock } = await import("bun:test");
  const saved = savedTemplate();
  const invoke = mock(async () => null);
  const restoreDesktop = mockDesktop(invoke);
  try {
    fetchMock.mockResolvedValueOnce(protobufListResponse([saved]));
    expect(await listTemplates()).toEqual([templateSummary(saved)]);
    fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "get"));
    expect(await getTemplate(saved.template_id)).toEqual(saved);
    fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "save", 201));
    expect(await saveTemplate(toDraft(saved))).toEqual(saved);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await deleteTemplate(saved.template_id);
    expect(fetchMock.mock.calls.map(([url, options]) => `${options?.method} ${url}`)).toEqual([
      "GET http://api.test:8000/template",
      `GET http://api.test:8000/template/${saved.template_id}`,
      "POST http://api.test:8000/template",
      `DELETE http://api.test:8000/template/${saved.template_id}`,
    ]);
    expect(invoke).not.toHaveBeenCalled();
  } finally {
    restoreDesktop();
  }
});
