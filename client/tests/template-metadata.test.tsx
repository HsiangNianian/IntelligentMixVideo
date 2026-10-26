/** 模板信息编辑回归：真实工作区经 HTTP 边界保存，检查失焦写入、校验与失败恢复。 */
import { expect, test } from "bun:test";
import { fromBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SaveTemplateRequestSchema, type SaveTemplateRequest } from "@/generated/imv/template/v1/template_pb";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { jsonTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

/** 可读取的云端持久化副本；断言实际消息中的 ID，失败不改副本。 */
function storage(failFirst = false) {
  let stored = { ...savedTemplate(), description: "原描述" };
  const writes: SaveTemplateRequest[] = [];
  const save = (payload: Uint8Array) => {
    const request = fromBinary(SaveTemplateRequestSchema, payload);
    writes.push(request);
    if (failFirst && writes.length === 1) throw new Error("模板名称已存在，请使用其他名称");
    stored = { ...stored, name: request.name, description: request.description ?? "" };
    return stored;
  };
  fetchMock.mockImplementation((async (url, init) => {
    if (!String(url).includes("/template")) throw new Error("未配置此请求");
    if (init?.method !== "POST") return jsonTemplateResponse(stored, "get");
    try { return jsonTemplateResponse(save(new Uint8Array(init.body as Uint8Array)), "save"); }
    catch (error) { return new Response(JSON.stringify({ detail: (error as Error).message }), { status: 409 }); }
  }) as typeof fetch);
  return { read: () => stored, writes, selection: { templateId: stored.template_id } };
}

// 场景：改名、清空描述后失焦自动保存，重新打开仍是同一 ID 与最新信息。
test("模板信息失焦保存并保持原 ID", async () => {
  const store = storage();
  const view = render(<TemplateWorkspace selection={store.selection} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑模板名称" }));
  const name = screen.getByRole("textbox", { name: "模板名称" });
  fireEvent.change(name, { target: { value: "  修改后的模板  " } });
  expect(store.writes).toHaveLength(0);
  expect(screen.getByText("有未保存的修改")).toBeTruthy();
  fireEvent.blur(name);
  await waitFor(() => expect(screen.getByLabelText("模板名称").textContent).toBe("修改后的模板"));
  await screen.findByText("已保存");
  fireEvent.click(screen.getByRole("button", { name: "编辑模板描述" }));
  const description = screen.getByRole("textbox", { name: "模板描述" });
  fireEvent.change(description, { target: { value: "" } });
  fireEvent.blur(description);
  await screen.findByText("已保存");
  expect(store.writes.map((item) => item.templateId)).toEqual([store.selection.templateId, store.selection.templateId]);
  expect(store.read().description).toBe("");
  view.unmount();
  render(<TemplateWorkspace selection={store.selection} onHome={() => {}} />);
  expect((await screen.findByLabelText("模板名称")).textContent).toBe("修改后的模板");
  expect(screen.getByLabelText("模板描述").textContent).toBe("暂无模板描述");
});

// 场景：空白名称、未改动的输入及 Escape 取消不写入；校验失败可继续修改。
test("模板信息拒绝空白名称，未修改与取消不保存", async () => {
  const store = storage();
  render(<TemplateWorkspace selection={store.selection} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑模板名称" }));
  fireEvent.blur(screen.getByRole("textbox", { name: "模板名称" }));
  expect(store.writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "编辑模板名称" }));
  const name = screen.getByRole<HTMLInputElement>("textbox", { name: "模板名称" });
  expect(name.maxLength).toBe(100);
  fireEvent.change(name, { target: { value: "   " } });
  fireEvent.blur(name);
  expect(screen.getByRole("alert").textContent).toBe("请输入模板名称");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  expect(store.writes).toHaveLength(0);
  fireEvent.keyDown(name, { key: "Escape" });
  expect(screen.getByLabelText("模板名称").textContent).toBe(store.read().name);
  fireEvent.click(screen.getByRole("button", { name: "编辑模板描述" }));
  const description = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "模板描述" });
  expect(description.maxLength).toBe(1000);
  fireEvent.change(description, { target: { value: "取消的描述" } });
  fireEvent.keyDown(description, { key: "Escape" });
  expect(store.writes).toHaveLength(0);
  expect(screen.getByLabelText("模板描述").textContent).toBe("原描述");
});

// 场景：重名被拒后保留新名称和已有文字草稿，再次修改并失焦可成功保存。
test("模板信息保存失败保留草稿，修正后可重试", async () => {
  const store = storage(true);
  render(<TemplateWorkspace selection={store.selection} onHome={() => {}} />);
  await screen.findByRole("button", { name: "编辑模板名称" });
  fireEvent.change(screen.getByLabelText("示例文字"), { target: { value: "保留的标题" } });
  fireEvent.click(screen.getByRole("button", { name: "编辑模板名称" }));
  const name = screen.getByRole("textbox", { name: "模板名称" });
  fireEvent.change(name, { target: { value: "重名模板" } });
  fireEvent.blur(name);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("模板名称已存在"));
  expect(screen.getByLabelText("模板名称").textContent).toBe("重名模板");
  expect(screen.getByLabelText<HTMLInputElement>("示例文字").value).toBe("保留的标题");
  expect(store.read().name).toBe("已有模板");
  fireEvent.click(screen.getByRole("button", { name: "编辑模板名称" }));
  const retry = screen.getByRole("textbox", { name: "模板名称" });
  fireEvent.change(retry, { target: { value: "可用名称" } });
  fireEvent.blur(retry);
  await screen.findByText("已保存");
  expect(store.read().name).toBe("可用名称");
  expect(store.writes[1].tracks?.tracks[0].editor?.title).toBe("保留的标题");
});
