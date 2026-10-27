/** 数字人效果配置的协议往返、关闭保留和编辑入口；bun run test，不访问 IMS。 */
import { expect, test } from "bun:test";
import { fromBinary, toJson } from "@bufbuild/protobuf";
import { AvatarEffectsSchema, SaveTemplateRequestSchema } from "@/generated/imv/template/v1/template_pb";
import { getTemplate, saveTemplate } from "@/features/templates/api";
import { newDraft, toDraft } from "@/features/templates/model";
import { defaultAvatarEffects, type AvatarEffects } from "@/features/templates/avatarEffects";
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
