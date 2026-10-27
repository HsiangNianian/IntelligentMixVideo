/** 同屏核心回归：协议保存与窗口几何；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fromBinary, toJson } from "@bufbuild/protobuf";
import { SaveTemplateRequestSchema, SplitScreenSchema } from "@/generated/imv/template/v1/template_pb";
import { saveTemplate, getTemplate } from "@/features/templates/api";
import { newDraft, toDraft } from "@/features/templates/model";
import { defaultSplitScreen, screenRect, type SplitScreen } from "@/features/templates/splitScreen";
import { savedTemplate, jsonTemplateResponse } from "./fixtures";
import { fetchMock } from "./setup";

// 保存用真实 Protobuf 编解码，验证零值和关闭参数完整往返，避免只测试 JS 内存副本。
test("同屏配置保存读取和另存草稿保留零值及独立窗口", async () => {
  const layout = { ...defaultSplitScreen(), enabled: true, backgroundColor: "#224466" };
  layout.avatar.radius = 0; layout.avatar.borderWidth = 0; layout.avatar.x = 0;
  let stored = savedTemplate();
  fetchMock.mockImplementation((async (_url, init) => {
    if (init?.method === "POST") {
      const message = fromBinary(SaveTemplateRequestSchema, new Uint8Array(init.body as ArrayBuffer));
      expect(message.splitScreen?.avatar?.radius).toBe(0);
      stored = { ...stored, split_screen: toJson(SplitScreenSchema, message.splitScreen!) as unknown as SplitScreen };
    }
    return jsonTemplateResponse(stored, init?.method === "POST" ? "save" : "get");
  }) as typeof fetch);
  const saved = await saveTemplate({ ...newDraft(), name: "同屏", split_screen: layout });
  expect(saved.split_screen).toEqual(layout);
  expect((await getTemplate(saved.template_id)).split_screen).toEqual(layout);
  const copy = toDraft(saved); copy.split_screen!.enabled = false;
  const disabled = await saveTemplate(copy, saved.template_id);
  expect(disabled.split_screen).toEqual({ ...layout, enabled: false });
  expect(saved.split_screen!.enabled).toBe(true);
});

// 与 Python 同一组输入的几何锚点，缩进后的圆角和边框仍围住实际画面。
test("完整显示采用可见图像矩形，裁剪填满采用配置矩形", () => {
  const window = { ...defaultSplitScreen().material, x: 10, y: 10, width: 80, height: 50, radius: 24, borderWidth: 6 };
  expect(screenRect(window, { width: 1920, height: 1080 })).toEqual({ x: 108, y: 429, width: 864, height: 486, radius: 24, border: 6 });
  expect(screenRect({ ...window, fit: "Cover" }, { width: 1920, height: 1080 })).toEqual({ x: 108, y: 192, width: 864, height: 960, radius: 24, border: 6 });
});
