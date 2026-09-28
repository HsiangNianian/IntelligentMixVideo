/** Sprite 回归：二进制发布类型、独立轨道编辑与业务参数隔离。 */
import { expect, spyOn, test } from "bun:test";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createRef, useState } from "react";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { SpriteAssetPicker, SpriteBindingsPanel } from "@/features/sprites/SpriteBindingsPanel";
import { SpritePreviewLayer, type SpritePreviewLayerHandle } from "@/features/sprites/SpritePreviewLayer";
import { previewWindow, type SpritePreviewCopy } from "@/features/sprites/preview";
import { publishSprite } from "@/features/sprites/api";
import {
  OperatorAccess,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  ScalarValueSchema,
  SpriteKind,
  SpritePlacementSchema,
  SpriteParameterSchema,
  SpriteSummarySchema,
  SpriteTarget,
  type SpritePlacement,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { fetchMock } from "./setup";

test("字幕发布使用独立 Sprite Protobuf 并声明关键词能力", async () => {
  const response = create(PublishSpriteResponseSchema, {
    sprite: create(SpriteSummarySchema, {
      spriteId: "sprite-1",
      name: "高亮字幕",
      kind: SpriteKind.TEXT,
      keywordsSupported: true,
    }),
  });
  fetchMock.mockResolvedValueOnce(
    new Response(toBinary(PublishSpriteResponseSchema, response), {
      headers: { "Content-Type": "application/x-protobuf" },
    }),
  );
  expect(
    (await publishSprite("accepted-version", "subtitle")).keywordsSupported,
  ).toBe(true);
  const request = fromBinary(
    PublishSpriteRequestSchema,
    new Uint8Array(fetchMock.mock.calls[0][1]?.body as Uint8Array),
  );
  expect(request.sourceVersionId).toBe("accepted-version");
  expect(request.kind).toBe(SpriteKind.TEXT);
  expect(request.textProp).toBe("0_text");
  expect(request.keywordsProp).toBe("highlightRanges");
});

/** Drive a real editable panel with local state so each operation re-renders its saved placements. */
function Editor() {
  const [placements, setPlacements] = useState<SpritePlacement[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [previewCopy, setPreviewCopy] = useState<Record<string, SpritePreviewCopy>>({});
  const catalog = [
    create(SpriteSummarySchema, {
      spriteId: "filter-1",
      name: "暖色滤镜",
      kind: SpriteKind.FILTER_OVERLAY,
      parameters: [
        create(SpriteParameterSchema, {
          key: "brightness",
          label: "亮度",
          access: OperatorAccess.VISIBLE_EDITABLE,
          defaultValue: create(ScalarValueSchema, {
            value: { case: "numberValue", value: 0.4 },
          }),
          minimum: 0,
          maximum: 2,
        }),
        create(SpriteParameterSchema, {
          key: "font_weight",
          label: "字重",
          access: OperatorAccess.VISIBLE_EDITABLE,
          defaultValue: create(ScalarValueSchema, {
            value: { case: "numberValue", value: 700 },
          }),
          allowedValues: [400, 700].map((weight) => create(ScalarValueSchema, {
            value: { case: "numberValue", value: weight },
          })),
        }),
      ],
    }),
  ];
  return (
    <>
      <SpriteAssetPicker
        catalog={catalog}
        count={placements.length}
        loading={false}
        onAdd={(placement) => {
          setPlacements((items) => [...items, placement]);
          setOpen(placement.id);
        }}
      />
      <SpriteBindingsPanel
        catalog={catalog}
        placements={placements}
        onChange={setPlacements}
        open={open}
        onOpenChange={setOpen}
        previewCopy={previewCopy}
        onPreviewCopyChange={(id, value) => setPreviewCopy((items) => ({ ...items, [id]: value }))}
      />
      <output data-testid="placements">
        {JSON.stringify(
          placements.map((item) => ({
            target: item.target,
            overrides: item.overrides.map((entry) => entry.value?.value),
          })),
        )}
      </output>
    </>
  );
}

test("滤镜 Sprite 保留独立作用对象与可编辑样式参数", async () => {
  render(<Editor />);
  const assets = within(screen.getByRole("region", { name: "Remotion Sprite 资产" }));
  fireEvent.keyDown(assets.getByRole("combobox", { name: "选择 Sprite" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("option", { name: "暖色滤镜" }));
  fireEvent.click(assets.getByRole("button", { name: "添加 Sprite" }));
  expect(screen.getByTestId("placements").textContent).toContain(
    `"target":${SpriteTarget.FILTER}`,
  );
  const brightness = screen.getByLabelText<HTMLInputElement>("亮度");
  fireEvent.change(brightness, { target: { value: "1.2" } });
  fireEvent.blur(brightness);
  await waitFor(() =>
    expect(screen.getByTestId("placements").textContent).toContain("1.2"),
  );
  fireEvent.keyDown(screen.getByRole("combobox", { name: "字重" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("option", { name: "常规" }));
  expect(screen.getByTestId("placements").textContent).toContain('"value":400');
  fireEvent.click(screen.getByRole("button", { name: "移除" }));
  expect(screen.getByTestId("placements").textContent).toBe("[]");
});

test("字幕示例窗口只占短句时间，片段边界使用演示位置", () => {
  const subtitle = create(SpritePlacementSchema, {
    id: "subtitle-1", target: SpriteTarget.SUBTITLE, startMode: "seconds", start: 4,
  });
  expect(previewWindow(subtitle, 10)).toEqual({ start: 4, end: 6 });
  const transition = create(SpritePlacementSchema, {
    id: "transition-1", target: SpriteTarget.TRANSITION, startMode: "seconds", duration: 1,
  });
  expect(previewWindow(transition, 10)).toEqual({ start: 4.5, end: 5.5 });
});

test("透明 Sprite 预览接收示例配置并跟随 IMS 时间定位", async () => {
  const sprite = create(SpriteSummarySchema, {
    spriteId: "sprite-1", name: "字幕高亮", kind: SpriteKind.TEXT,
    canvas: { width: 1080, height: 1920, fps: 30, previewFrames: 150 },
    keywordsSupported: true,
  });
  const placement = create(SpritePlacementSchema, {
    id: "placement-1", spriteId: sprite.spriteId, target: SpriteTarget.SUBTITLE,
    startMode: "seconds", start: 0,
  });
  const ref = createRef<SpritePreviewLayerHandle>();
  render(<div><SpritePreviewLayer ref={ref} placements={[placement]} catalog={[sprite]}
    duration={10} canvas={{ width: 1920, height: 1080 }} time={0} copy={{}} /></div>);
  const frame = screen.getByTitle<HTMLIFrameElement>("字幕高亮 实时叠加预览");
  const post = spyOn(frame.contentWindow!, "postMessage");
  const channel = new URL(frame.src).hash.slice(1);
  act(() => window.dispatchEvent(new MessageEvent("message", {
    source: frame.contentWindow, data: { type: "imv-sprite-ready", channel },
  })));
  await waitFor(() => expect(post.mock.calls.some(([message]) =>
    message.type === "imv-sprite-config" && message.text.includes("示例字幕") && message.totalFrames === 60,
  )).toBe(true));
  const configured = post.mock.calls.find(([message]) => message.type === "imv-sprite-config")![0];
  act(() => window.dispatchEvent(new MessageEvent("message", {
    source: frame.contentWindow, data: { type: "imv-sprite-rendered", channel, requestId: configured.requestId },
  })));
  act(() => ref.current!.seek(1));
  await waitFor(() => expect(post.mock.calls.some(([message]) => message.type === "imv-sprite-seek" && message.frame === 30)).toBe(true));
  expect(frame.style.visibility).toBe("visible");
  act(() => ref.current!.seek(3));
  await waitFor(() => expect(frame.style.visibility).toBe("hidden"));
  post.mockRestore();
});
