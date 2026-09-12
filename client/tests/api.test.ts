/** 模板 HTTP 核心测试：请求契约、必要校验与错误展示；fetch 由 setup.ts 隔离。 */
import { expect, test } from "bun:test";
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
