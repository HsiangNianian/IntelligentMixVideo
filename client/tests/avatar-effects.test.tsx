/** 数字人效果配置的协议往返、关闭保留和编辑入口；bun run test，不访问 IMS。 */
import { expect, test } from "bun:test";
import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { fromBinary, toJson } from "@bufbuild/protobuf";
import { AvatarEffectsSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
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
      stored = { ...stored, avatar_effects: toJson(AvatarEffectsSchema, message.avatarEffects!) as unknown as AvatarEffects };
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const saved = await saveTemplate({ ...newDraft(), name: "自动开场", avatar_effects: config });
  expect(saved.avatar_effects).toEqual(config);
  expect((await getTemplate(saved.template_id)).avatar_effects).toEqual(config);
  const draft = toDraft(saved);
  draft.avatar_effects!.openingEnabled = false;
  draft.avatar_effects!.removeBackground = false;
  const disabled = await saveTemplate(draft, saved.template_id);
  expect(disabled.avatar_effects).toEqual({ ...config, openingEnabled: false, removeBackground: false });
  expect(saved.avatar_effects?.openingEnabled).toBe(true);
});

// 不含新字段的旧模板继续读写；编辑中的非法数值须在网络写入前被拒绝。
test("兼容旧模板，无效开场时长不发保存请求", async () => {
  fetchMock.mockResolvedValue(jsonTemplateResponse(savedTemplate(), "get"));
  expect((await getTemplate(savedTemplate().template_id)).avatar_effects).toBeUndefined();
  fetchMock.mockClear();
  await expect(saveTemplate({ ...newDraft(), name: "错误时长", avatar_effects: { ...defaultAvatarEffects(), openingDuration: NaN } })).rejects.toThrow("开场时长");
  expect(fetchMock).not.toHaveBeenCalled();
});

// 开关只影响是否使用，不能清除用户已填的秒数或抠像偏好。
test("开场开关保留时长，同屏关闭时不可修改抠像且说明预览范围", () => {
  function Editor({ sameScreen }: { sameScreen: boolean }) {
    const [config, setConfig] = useState({ ...defaultAvatarEffects(), removeBackground: true });
    return <AvatarEffectsEditor value={config} sameScreen={sameScreen} onChange={setConfig} />;
  }
  const view = render(<Editor sameScreen />);
  fireEvent.click(screen.getByText("启用圆形开场"));
  fireEvent.change(screen.getByLabelText("开场时长（秒）"), { target: { value: "3.5" } });
  fireEvent.click(screen.getByText("启用圆形开场"));
  fireEvent.click(screen.getByText("启用圆形开场"));
  expect(screen.getByLabelText<HTMLInputElement>("开场时长（秒）").value).toBe("3.5");
  view.rerender(<Editor sameScreen={false} />);
  const matting = screen.getByLabelText<HTMLInputElement>("同屏时去除数字人背景");
  expect(matting.disabled).toBe(true);
  expect(matting.checked).toBe(true);
  expect(screen.getByText(/自动圆形开场和抠像效果以成片为准/)).toBeTruthy();
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
  fireEvent.click(await screen.findByText("启用圆形开场"));
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
  expect(screen.queryByLabelText("素材宽度 %")).toBeNull();
  expect(screen.queryByRole("option", { name: "小窗画中画" })).toBeNull();
  const checkbox = screen.getByLabelText<HTMLInputElement>("同屏时去除数字人背景");
  expect(checkbox.checked).toBe(mode === "overlay");
  expect(checkbox.disabled).toBe(mode === "circle");
  expect(screen.getByLabelText<HTMLInputElement>("头部圆形留量（倍）").disabled).toBe(mode !== "circle");
  fireEvent.submit(screen.getByRole("button", { name: "保存模板" }).closest("form")!);
  await waitFor(() => expect(request).toBeDefined());
  expect(request!.splitScreen?.layout).toBe(mode);
  expect(request!.splitScreen?.avatar).toMatchObject({ x: 0, y: 70 });
  expect(request!.avatarEffects?.removeBackground).toBe(mode === "overlay");
});
