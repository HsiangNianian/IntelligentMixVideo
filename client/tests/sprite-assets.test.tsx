/** Remotion 资产核心测试：版本卡片一键保存到独立资产库；执行 bun run test。 */
import { expect, test } from "bun:test";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { fireEvent, render, screen } from "@testing-library/react";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import {
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SpriteKind,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { remotionVersion } from "./remotion-fixtures";
import { fetchMock } from "./setup";

const HEADERS = { "Content-Type": "application/x-protobuf" };

/** 一个 3 秒（90 帧、30 FPS）的资产摘要。 */
function summary(id = "sprite-1", kind = SpriteKind.VIDEO_OVERLAY, keywords = false) {
  return {
    spriteId: id,
    name: "霓虹标题",
    kind,
    keywordsSupported: keywords,
    previewUrl: `/api/sprites/${id}/preview`,
    canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 90 },
  };
}

/** 构造二进制响应。 */
function binary(schema: Parameters<typeof toBinary>[0], message: unknown, status = 200): Response {
  return new Response(Uint8Array.from(toBinary(schema, message as never)), { status, headers: HEADERS });
}

// 场景：新生成的组合版本一键保存为资产，不弹窗、不要求选择；请求只带版本和视频叠加类型。
test("版本卡片一键把成功版本保存为 Sprite 资产", async () => {
  let request: ReturnType<typeof fromBinary<typeof PublishSpriteRequestSchema>> | undefined;
  fetchMock.mockImplementation((async (url, init) => {
    expect(new URL(String(url)).pathname).toBe("/api/sprites/publish");
    request = fromBinary(PublishSpriteRequestSchema, new Uint8Array(init!.body as Uint8Array));
    return binary(PublishSpriteResponseSchema, create(PublishSpriteResponseSchema, { sprite: summary() }));
  }) as typeof fetch);
  const version = remotionVersion("version-2");
  version.spec.sprite_kind = "composition";
  render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  await screen.findByText("已保存到资产「霓虹标题」。");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(request).toMatchObject({ sourceVersionId: "version-2", kind: SpriteKind.VIDEO_OVERLAY, textProp: "", keywordsProp: "" });
});

// 场景：旧的文字版本沿用文字类型并自动取嵌套的标题字段；失败显示服务端原因，未保存参数时入口禁用。
test("旧文字版本自动选择文字字段，失败显示原因，未保存参数时禁用", async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "accepted artifact unavailable" }), { status: 409 }));
  const version = remotionVersion("version-2");
  version.spec.sprite_kind = "text";
  version.candidate.config_schema = { properties: { title_main: { type: "object", properties: { title: { type: "string" }, fontSize: { type: "number" } } } } };
  const { rerender } = render(<VersionCard version={version} selected={false} latest disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "保存到资产" }));
  expect((await screen.findByText("accepted artifact unavailable")).getAttribute("role")).toBe("alert");
  const body = fetchMock.mock.calls[0][1]!.body as Uint8Array;
  expect(fromBinary(PublishSpriteRequestSchema, new Uint8Array(body))).toMatchObject({ kind: SpriteKind.TEXT, textProp: "title_main.title" });
  rerender(<VersionCard version={version} selected={false} latest disabled previewDisabled={false} onPreview={() => {}} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "保存到资产" }).disabled).toBe(true);
});
