/** 模板 HTTP 核心测试：请求契约、必要校验与错误展示；fetch 由 setup.ts 隔离。 */
import { expect, spyOn, test } from "bun:test";
import { fromBinary, toBinary } from "@bufbuild/protobuf";
import { GetTemplateResponseSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { deleteTemplate, getTemplate, listTemplates, saveTemplate } from "@/features/templates/api";
import { newDraft, toDraft } from "@/features/templates/model";
import { protobufListResponse, protobufTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock, mockDesktop } from "./setup";

// 测试创建和更新都使用 POST /template，只有更新带 ID，名称说明被修剪且效果去重。
test.each([undefined, "existing-id"])("保存请求正确区分创建与更新：%s", async (id) => {
  const saved = savedTemplate();
  const draft = toDraft(saved);
  draft.name = "  我的模板  ";
  draft.description = "  说明  ";
  draft.tracks[1].editor.subtitleIn = "in/fade_in";
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "save", id ? 200 : 201));
  expect(await saveTemplate(draft, id)).toEqual(saved);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api.test:8000/template");
  expect(options?.method).toBe("POST");
  expect(options?.headers).toEqual({ Accept: "application/x-protobuf", "Content-Type": "application/x-protobuf" });
  const body = fromBinary(SaveTemplateRequestSchema, new Uint8Array(options?.body as Uint8Array));
  expect(body).toMatchObject({ name: "我的模板", description: "说明", effectIds: ["in/fade_in"] });
  if (id) expect(body.templateId).toBe(id);
  else expect(body.templateId).toBeUndefined();
  expect(body).not.toHaveProperty("effects");
  expect(body).not.toHaveProperty("editor");
  expect(draft.name).toBe("  我的模板  ");
});

// 测试名称为空或没有选择效果时直接提示，不发送无效请求。
test("保存前检查名称和所选效果", async () => {
  const draft = newDraft();
  await expect(saveTemplate(draft)).rejects.toThrow("请输入模板名称");
  draft.name = "新模板";
  await expect(saveTemplate(draft)).rejects.toThrow("请至少选择一个效果");
  expect(fetchMock).not.toHaveBeenCalled();
});

// 测试列表与详情响应透传、路径 ID 编码，以及删除成功的 204 不尝试解析 JSON。
test("读取与删除遵循接口契约", async () => {
  const saved = savedTemplate();
  fetchMock.mockResolvedValueOnce(protobufListResponse([saved]));
  expect(await listTemplates()).toEqual([saved]);
  fetchMock.mockResolvedValueOnce(protobufTemplateResponse(saved, "get"));
  expect(await getTemplate("id/with space")).toEqual(saved);
  expect(fetchMock.mock.calls[1][0]).toBe("http://api.test:8000/template/id%2Fwith%20space");
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  expect(await deleteTemplate(saved.template_id)).toBeUndefined();
  expect(fetchMock.mock.calls[2][0]).toBe(`http://api.test:8000/template/${saved.template_id}`);
  expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
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

// 测试仅服务不可用时建议本地环境，普通业务错误保持原文；浏览器提示桌面限制。
test.each([400, 404, 409, 422, 500, 502, 503, 504])("按 HTTP 状态提示本地环境：%s", async (status) => {
  fetchMock.mockResolvedValueOnce(Response.json({ detail: "服务端提示" }, { status }));
  await expect(listTemplates()).rejects.toThrow(new Error(
    status >= 500 ? "服务端提示 可使用桌面客户端切换到本地环境。" : "服务端提示",
  ));
});

// 测试网关返回空正文或普通文本时仍展示 HTTP 状态与本地环境提示。
test.each([null, "upstream unavailable"])("非 JSON 的 503 响应仍提示本地环境：%s", async (body) => {
  fetchMock.mockResolvedValueOnce(new Response(body, { status: 503 }));
  await expect(listTemplates()).rejects.toThrow("HTTP 503 可使用桌面客户端切换到本地环境。");
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
    : "请求超时，草稿已保留。保存结果可能已写入，请刷新列表确认后再重试。可使用桌面客户端切换到本地环境。");
});

// 回归：本地操作走官方 IPC，云端走 HTTP；桌面服务故障显示本地模板提示。
test("本地和云端存储严格分流", async () => {
  const { mock } = await import("bun:test");
  const saved = savedTemplate();
  const invoke = mock(async (_command: string, args: Record<string, unknown>): Promise<unknown> => {
    if (args.operation === "list") return [saved];
    if (args.operation === "delete") return null;
    return saved;
  });
  const restoreDesktop = mockDesktop(invoke);
  try {
    expect(window).not.toHaveProperty("__TAURI__");
    expect(await listTemplates(undefined, "local")).toEqual([saved]);
    expect(await getTemplate(saved.template_id, "local")).toEqual(saved);
    expect(await saveTemplate(toDraft(saved), undefined, "local")).toEqual(saved);
    await deleteTemplate(saved.template_id, "local");
    expect(invoke.mock.calls.every(([command]) => command === "local_templates")).toBe(true);
    expect(invoke.mock.calls.map(([, args]) => args.operation)).toEqual(["list", "get", "save", "delete"]);
    const saveArgs = invoke.mock.calls[2][1];
    expect(saveArgs.operation).toBe("save");
    expect(saveArgs.id).toBeUndefined();
    expect(fromBinary(SaveTemplateRequestSchema, Uint8Array.from(saveArgs.draft as number[])))
      .toMatchObject({ name: saved.name, description: saved.description });
    expect(fetchMock).not.toHaveBeenCalled();
    invoke.mockRejectedValueOnce("磁盘空间不足");
    await expect(saveTemplate(toDraft(saved), undefined, "local")).rejects.toThrow("磁盘空间不足");
    fetchMock.mockResolvedValueOnce(protobufListResponse([]));
    expect(await listTemplates(undefined, "cloud")).toEqual([]);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(listTemplates(undefined, "cloud")).rejects.toThrow("HTTP 503 可返回主页选择或创建本地模板。");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  } finally {
    restoreDesktop();
  }
});

// 测试普通浏览器选择本地时明确提示使用桌面，不偷偷写入云端或浏览器缓存。
test("浏览器无法调用本地文件存储", async () => {
  await expect(listTemplates(undefined, "local")).rejects.toThrow("桌面客户端");
  expect(fetchMock).not.toHaveBeenCalled();
});
