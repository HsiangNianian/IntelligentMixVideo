/** 数字人效果配置的协议往返、关闭保留和编辑入口；bun run test，不访问 IMS。 */
import { expect, mock, test } from "bun:test";
import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { fromBinary, toJson } from "@bufbuild/protobuf";
import { AvatarEffectsSchema, AvatarPreviewSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { getTemplate, saveTemplate } from "@/features/templates/api";
import { newDraft, toDraft } from "@/features/templates/model";
import { defaultAvatarEffects, type AvatarEffects } from "@/features/templates/avatarEffects";
import { AvatarEffectsEditor } from "@/features/templates/AvatarEffectsEditor";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { jsonTemplateResponse, savedTemplate } from "./fixtures";
import { fetchMock } from "./setup";

// 请求经真实 Protobuf 编解码再作为服务端响应读取，验证样式和显式零值不会丢失。
test("自动开场和抠像保存读取、另存、关闭后保留参数", async () => {
  const config = { ...defaultAvatarEffects(), openingEnabled: true, openingDuration: 3, circleScale: 1.8, feather: 0, removeBackground: true };
  let stored = savedTemplate();
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      const message = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
      expect(message.avatarEffects?.feather).toBe(0);
      expect(message.avatarPreview).toMatchObject({ x: 0, y: 100, source: 2 });
      stored = { ...stored, avatar_effects: toJson(AvatarEffectsSchema, message.avatarEffects!) as unknown as AvatarEffects, avatar_preview: { x: message.avatarPreview!.x!, y: message.avatarPreview!.y!, source: message.avatarPreview!.source! } };
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const saved = await saveTemplate({ ...newDraft(), name: "自动开场", avatar_effects: config, avatar_preview: { x: 0, y: 100, source: 2 } });
  expect(saved.avatar_effects).toEqual(config);
  const restored = await getTemplate(saved.template_id);
  expect(restored.avatar_effects).toEqual(config);
  expect(restored.avatar_preview).toEqual({ x: 0, y: 100, source: 2 });
  const draft = toDraft(saved);
  draft.avatar_effects!.openingEnabled = false;
  draft.avatar_effects!.removeBackground = false;
  const disabled = await saveTemplate(draft, saved.template_id);
  expect(disabled.avatar_effects).toEqual({ ...config, openingEnabled: false, removeBackground: false });
  expect(saved.avatar_effects?.openingEnabled).toBe(true);
});

// 不含新字段的旧模板继续读写；编辑中的非法数值须在网络写入前被拒绝。
test("兼容旧模板，无效数字人参数不发保存请求", async () => {
  fetchMock.mockResolvedValue(jsonTemplateResponse(savedTemplate(), "get"));
  expect((await getTemplate(savedTemplate().template_id)).avatar_effects).toBeUndefined();
  fetchMock.mockClear();
  for (const [key, value, message] of [
    ["openingDuration", NaN, "开场时长"], ["circleScale", NaN, "圆形留量"],
    ["feather", NaN, "羽化"], ["feather", 0.3, "羽化"],
  ] as const) {
    await expect(saveTemplate({ ...newDraft(), name: "无效输入", avatar_effects: { ...defaultAvatarEffects(), [key]: value } })).rejects.toThrow(message);
  }
  expect(fetchMock).not.toHaveBeenCalled();
});

// 保存后的取景在重新打开模板时恢复，修改进入未保存状态，真实效果配置仍独立。
test("取景随模板保存并恢复，来源切换重置圆心，参数只在右侧", async () => {
  let stored = { ...savedTemplate(), avatar_effects: { ...defaultAvatarEffects(), openingEnabled: true }, avatar_preview: { x: 12, y: 38, source: 2 } };
  fetchMock.mockImplementation((async (url, init) => {
    if (init?.method === "POST") {
      const request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
      expect(toJson(AvatarPreviewSchema, request.avatarPreview!)).toEqual({ x: 27, y: 43, source: 1 });
      stored = { ...stored, avatar_preview: { x: request.avatarPreview!.x!, y: request.avatarPreview!.y!, source: request.avatarPreview!.source! } };
      return jsonTemplateResponse(stored, "save");
    }
    return String(url).includes(`/template/${stored.template_id}`) ? jsonTemplateResponse(stored, "get") : new Response(JSON.stringify({ sprites: [] }), { headers: { "Content-Type": "application/json" } });
  }) as typeof fetch);
  const editing = mock();
  const view = render(<TemplateWorkspace selection={{ templateId: stored.template_id }} onHome={() => {}} onEditingState={editing} />);
  await screen.findByRole("button", { name: "编辑圆形蒙版" });
  expect(screen.queryByLabelText("预览取景 X（%）")).toBeNull();
  await waitFor(() => expect(editing.mock.calls.at(-1)?.[0]?.dirty).toBe(false));
  fireEvent.click(screen.getByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "添加圆形蒙版" }));
  expect(screen.getByText("当前位置仅用于预览，成片会自动定位人脸。")).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 X（%）").value).toBe("12");
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 Y（%）").value).toBe("38");
  expect(screen.getByLabelText<HTMLSelectElement>("数字人预览来源").value).toBe("2");
  expect(screen.getByLabelText("预览取景 X（%）").closest("aside")?.className).toContain("template-workspace-inspector");
  expect(within(screen.getByLabelText("实时预览")).queryByLabelText("数字人预览来源")).toBeNull();
  fireEvent.change(screen.getByLabelText("数字人预览来源"), { target: { value: "1" } });
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 X（%）").value).toBe("50");
  fireEvent.change(screen.getByLabelText("预览取景 X（%）"), { target: { value: "27" } });
  fireEvent.change(screen.getByLabelText("预览取景 Y（%）"), { target: { value: "43" } });
  expect(editing.mock.calls.at(-1)![0].dirty).toBe(true);
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(editing.mock.calls.at(-1)?.[0]?.dirty).toBe(false));
  view.unmount();
  render(<TemplateWorkspace selection={{ templateId: stored.template_id }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑圆形蒙版" }));
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 X（%）").value).toBe("27");
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 Y（%）").value).toBe("43");
  expect(screen.getByLabelText<HTMLSelectElement>("数字人预览来源").value).toBe("1");
});

// 时长直接更新已添加的开场；移除后沿用共用按钮关闭设置。
test("开场时长更新后可移除对象并关闭设置", () => {
  const remove = mock(), close = mock();
  function Editor() {
    const [config, setConfig] = useState({ ...defaultAvatarEffects(), openingEnabled: true });
    return <AvatarEffectsEditor value={config} onChange={setConfig} onClose={close} onRemove={remove} />;
  }
  render(<Editor />);
  fireEvent.change(screen.getByLabelText("开场时长（秒）"), { target: { value: "3.5" } });
  expect(screen.getByLabelText<HTMLInputElement>("开场时长（秒）").value).toBe("3.5");
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  expect(remove).toHaveBeenCalledTimes(1);
  expect(close).toHaveBeenCalledTimes(1);
});

// 从真实工作区操作到保存请求，确保布局示意去掉边框时不会改写保存的原窗口样式。
test("工作区保存新增配置，抠像预览不覆盖原窗口圆角和边框", async () => {
  let request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method !== "POST") return new Response(JSON.stringify({ sprites: [] }), { headers: { "Content-Type": "application/json" } });
    request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
    return jsonTemplateResponse({ ...savedTemplate(), name: "自动数字人", avatar_effects: toJson(AvatarEffectsSchema, request.avatarEffects!) as unknown as AvatarEffects }, "save");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: null, name: "自动数字人", description: "" }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("tab", { name: "蒙版" }));
  fireEvent.click(screen.getByRole("button", { name: "添加圆形蒙版" }));
  fireEvent.change(screen.getByLabelText("开场时长（秒）"), { target: { value: "2.5" } });
  fireEvent.click(screen.getByText("启用同屏"));
  fireEvent.click(screen.getByText("同屏时去除数字人背景"));
  expect(screen.getByLabelText("数字人显示方式")).toBeTruthy();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  expect(request!.avatarEffects?.openingDuration).toBe(2.5);
  expect(request!.avatarEffects?.removeBackground).toBe(true);
  expect(request!.splitScreen?.avatar?.radius).toBe(30);
  expect(request!.splitScreen?.avatar?.borderWidth).toBe(6);
});

// 从实际工作区切换布局并提交协议，确保隐藏的旧素材框不会成为新布局的实际尺寸。
test.each(["overlay", "circle"] as const)("工作区选择 %s 后保存模式和 X/Y，去背景开关随模式生效", async (mode) => {
  let request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method !== "POST") return new Response(JSON.stringify({ sprites: [] }), { headers: { "Content-Type": "application/json" } });
    request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
    return jsonTemplateResponse(savedTemplate(), "save");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: null, name: "叠加布局", description: "" }} onHome={() => {}} />);
  fireEvent.click(await screen.findByText("启用同屏"));
  fireEvent.change(screen.getByLabelText("同屏布局"), { target: { value: mode } });
  fireEvent.change(screen.getByLabelText("数字人X %"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("数字人Y %"), { target: { value: "70" } });
  expect(screen.queryByRole("button", { name: "编辑圆形蒙版" })).toBeNull();
  if (mode === "circle") {
    fireEvent.change(screen.getByLabelText("预览取景 X（%）"), { target: { value: "27" } });
    fireEvent.change(screen.getByLabelText("预览取景 Y（%）"), { target: { value: "43" } });
    fireEvent.change(screen.getByLabelText("头部圆形留量（倍）"), { target: { value: "1.9" } });
    fireEvent.change(screen.getByLabelText("圆边羽化"), { target: { value: "0.12" } });
  }
  expect(screen.queryByLabelText("素材宽度 %")).toBeNull();
  expect(screen.queryByRole("option", { name: "小窗画中画" })).toBeNull();
  const checkbox = screen.getByLabelText<HTMLInputElement>("同屏时去除数字人背景");
  expect(checkbox.checked).toBe(mode === "overlay");
  expect(checkbox.disabled).toBe(mode === "circle");
  if (mode === "circle") expect(screen.getByLabelText("头部圆形留量（倍）").closest("details")?.textContent).toContain("素材与数字人同屏");
  else expect(screen.queryByLabelText("头部圆形留量（倍）")).toBeNull();
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  expect(request!.splitScreen?.layout).toBe(mode);
  expect(request!.splitScreen?.avatar).toMatchObject({ x: 0, y: 70 });
  expect(request!.avatarEffects?.removeBackground).toBe(mode === "overlay");
  if (mode === "circle") {
    expect(toJson(AvatarEffectsSchema, request!.avatarEffects!)).toEqual({ openingEnabled: false, openingDuration: 2, circleScale: 1.9, feather: 0.12, removeBackground: false });
    expect(toJson(AvatarPreviewSchema, request!.avatarPreview!)).toEqual({ x: 27, y: 43, source: 0 });
  }
});

// 两种蒙版可共存；移除圆形开场后保留上下蒙版、头像及共享参数。
test("添加和移除圆形开场，保留上下蒙版、同屏头像、取景与参数，重加不重复", async () => {
  let request: ReturnType<typeof fromBinary<typeof SaveTemplateRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method !== "POST") return new Response(JSON.stringify({ sprites: [] }), { headers: { "Content-Type": "application/json" } });
    request = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
    return jsonTemplateResponse(savedTemplate(), "save");
  }) as typeof fetch);
  render(<TemplateWorkspace selection={{ templateId: null, name: "蒙版入口", description: "" }} onHome={() => {}} />);
  fireEvent.click(await screen.findByRole("tab", { name: "蒙版" }));
  expect(screen.queryByRole("button", { name: "编辑圆形蒙版" })).toBeNull();
  expect(screen.queryByLabelText("圆形蒙版设置")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "选择上下蒙版" }));
  expect(screen.getByRole("button", { name: "编辑上下蒙版对象" })).toBeDefined();
  const card = screen.getByRole("button", { name: "添加圆形蒙版" });
  expect(card.textContent).toBe("圆形蒙版点击添加，开场显示");
  fireEvent.click(card);
  expect(screen.getByRole("button", { name: "编辑圆形蒙版" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.change(screen.getByLabelText("开场时长（秒）"), { target: { value: "3.5" } });
  fireEvent.change(screen.getByLabelText("头部圆形留量（倍）"), { target: { value: "2.1" } });
  fireEvent.change(screen.getByLabelText("预览取景 X（%）"), { target: { value: "27" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭圆形蒙版设置" }));
  expect(screen.queryByLabelText("圆形蒙版设置")).toBeNull();
  fireEvent.click(card);
  expect(screen.getAllByRole("button", { name: "编辑圆形蒙版" })).toHaveLength(1);
  expect(screen.getByLabelText<HTMLInputElement>("开场时长（秒）").value).toBe("3.5");
  fireEvent.click(screen.getByLabelText("启用同屏"));
  fireEvent.change(screen.getByLabelText("同屏布局"), { target: { value: "circle" } });
  fireEvent.click(screen.getByRole("button", { name: "移除当前画面对象" }));
  expect(screen.queryByRole("button", { name: "编辑圆形蒙版" })).toBeNull();
  expect(screen.queryByLabelText("圆形蒙版设置")).toBeNull();
  expect(screen.getByRole("button", { name: "编辑上下蒙版对象" })).toBeDefined();
  expect(screen.getByLabelText<HTMLSelectElement>("同屏布局").value).toBe("circle");
  expect(screen.getByLabelText<HTMLInputElement>("启用同屏").checked).toBe(true);
  expect(screen.getByLabelText<HTMLInputElement>("头部圆形留量（倍）").value).toBe("2.1");
  expect(screen.getByLabelText<HTMLInputElement>("预览取景 X（%）").value).toBe("27");
  expect(screen.getByLabelText("预览取景 X（%）").closest("details")?.textContent).toContain("素材与数字人同屏");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  expect(request!.avatarEffects).toMatchObject({ openingEnabled: false, openingDuration: 3.5, circleScale: 2.1 });
  expect(request!.splitScreen).toMatchObject({ enabled: true, layout: "circle" });
  expect(request!.frameMask?.top?.enabled).toBe(true);
});
