/** 模板 HTTP 核心测试：请求契约、必要校验与错误展示；fetch 由 setup.ts 隔离。 */
import { expect, spyOn, test } from "bun:test";
import { deleteTemplate, getTemplate, listTemplates, saveTemplate } from "@/features/templates/api";
import { newDraft, toDraft } from "@/features/templates/model";
import { savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

// 测试创建和更新都使用 POST /template，只有更新带 ID，名称说明被修剪且效果去重。
test.each([undefined, "existing-id"])("保存请求正确区分创建与更新：%s", async (id) => {
  const saved = savedTemplate();
  const draft = toDraft(saved);
  draft.name = "  我的模板  ";
  draft.description = "  说明  ";
  draft.editor.subtitleIn = "in/fade_in";
  fetchMock.mockResolvedValueOnce(Response.json(saved, { status: id ? 200 : 201 }));
  expect(await saveTemplate(draft, id)).toEqual(saved);
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("http://api.test:8000/template");
  expect(options?.method).toBe("POST");
  expect(options?.headers).toEqual({ "Content-Type": "application/json" });
  const body = JSON.parse(String(options?.body));
  expect(body).toMatchObject({ name: "我的模板", description: "说明", effect_ids: ["in/fade_in"] });
  if (id) expect(body.template_id).toBe(id);
  else expect(body).not.toHaveProperty("template_id");
  expect(body).not.toHaveProperty("effects");
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
  fetchMock.mockResolvedValueOnce(Response.json([saved]));
  expect(await listTemplates()).toEqual([saved]);
  fetchMock.mockResolvedValueOnce(Response.json(saved));
  expect(await getTemplate("id/with space")).toEqual(saved);
  expect(fetchMock.mock.calls[1][0]).toBe("http://api.test:8000/template/id%2Fwith%20space");
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  expect(await deleteTemplate(saved.template_id)).toBeUndefined();
  expect(fetchMock.mock.calls[2][0]).toBe(`http://api.test:8000/template/${saved.template_id}`);
  expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
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

// 测试本地四种操作仅调用桌面 IPC，云端仍使用原 HTTP；IPC 失败保留错误原因。
test("本地和云端存储严格分流", async () => {
  const { mock } = await import("bun:test");
  const saved = savedTemplate();
  const invoke = mock(async (_command: string, args: Record<string, unknown>): Promise<unknown> => {
    if (args.operation === "list") return [saved];
    if (args.operation === "delete") return null;
    return saved;
  });
  window.__TAURI__ = { core: { invoke: invoke as NonNullable<Window["__TAURI__"]>["core"]["invoke"] } };
  try {
    expect(await listTemplates(undefined, "local")).toEqual([saved]);
    expect(await getTemplate(saved.template_id, "local")).toEqual(saved);
    expect(await saveTemplate(toDraft(saved), undefined, "local")).toEqual(saved);
    await deleteTemplate(saved.template_id, "local");
    expect(invoke.mock.calls.map(([, args]) => args.operation)).toEqual(["list", "get", "save", "delete"]);
    expect(invoke.mock.calls[2][1]).toEqual({ operation: "save", id: undefined, draft: toDraft(saved) });
    expect(fetchMock).not.toHaveBeenCalled();
    invoke.mockRejectedValueOnce("磁盘空间不足");
    await expect(saveTemplate(toDraft(saved), undefined, "local")).rejects.toThrow("磁盘空间不足");
    fetchMock.mockResolvedValueOnce(Response.json([]));
    expect(await listTemplates(undefined, "cloud")).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally {
    delete window.__TAURI__;
  }
});

// 测试普通浏览器选择本地时明确提示使用桌面，不偷偷写入云端或浏览器缓存。
test("浏览器无法调用本地文件存储", async () => {
  await expect(listTemplates(undefined, "local")).rejects.toThrow("桌面客户端");
  expect(fetchMock).not.toHaveBeenCalled();
});
