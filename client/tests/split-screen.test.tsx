/** 同屏核心回归：协议保存、示例角色、SDK 几何与基础预览；执行 bun run test。 */
import { expect, test } from "bun:test";
import { createRef, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { fromBinary, toJson } from "@bufbuild/protobuf";
import { SaveTemplateRequestSchema, SplitScreenSchema } from "@/generated/imv/template/v1/template_pb";
import { saveTemplate, getTemplate } from "@/features/templates/api";
import { newDraft, toDraft, type Draft } from "@/features/templates/model";
import { defaultSplitScreen, screenRect, type SplitScreen } from "@/features/templates/splitScreen";
import { SplitScreenEditor } from "@/features/templates/SplitScreenEditor";
import { buildTimeline, buildPreviewRows, playerTimeline } from "@/features/templates/timeline";
import { previewContext, resolveTemplate } from "@/features/templates/tracks";
import { previewTransitions } from "@/features/templates/transitions";
import { BasicVideoPreview } from "@/features/templates/BasicVideoPreview";
import type { PlaybackHandle } from "@/features/templates/previewClock";
import { sampleMedia, savedTemplate, jsonTemplateResponse } from "./fixtures";
import { fetchMock } from "./setup";

// 保存用真实 Protobuf 编解码，验证零值和关闭参数完整往返，避免只测试 JS 内存副本。
test.each(["split", "overlay", "circle"] as const)("同屏 %s 配置保存读取和另存草稿保留零值及独立窗口", async (mode) => {
  const layout = { ...defaultSplitScreen(), layout: mode, enabled: true, backgroundColor: "#224466" };
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

// 角色选择不改变总时长；素材与数字人使用独立几何，图层顺序及字幕位置可用于 SDK。
test("SDK 同屏布局保持时长、角色和图层，关闭后恢复普通预览", () => {
  const draft: Draft = { ...newDraft(), split_screen: { ...defaultSplitScreen(), enabled: true } };
  const resolved = resolveTemplate(draft, previewContext(sampleMedia));
  const timeline = buildTimeline(draft, [], sampleMedia, resolved, [0, 1, 2], 1);
  expect(timeline.previewVideoTracks[0].VideoTrackClips.at(-1)?.TimelineOut).toBe(resolved.duration);
  expect(buildPreviewRows(timeline).filter((row) => row.actions[0]?.effectId === "video")).toHaveLength(3);
  const [background, material, avatar, border] = timeline.VideoTracks;
  expect(background.VideoTrackClips[0].Type).toBe("Image");
  expect(material.VideoTrackClips.every((clip) => clip.MediaURL !== sampleMedia[1].url)).toBe(true);
  expect(avatar.VideoTrackClips.every((clip) => clip.MediaURL === sampleMedia[1].url)).toBe(true);
  const mask = new URL(avatar.VideoTrackClips[0].MaskVideoURL!);
  expect(mask.pathname).toBe("/template/preview-mask");
  expect(mask.searchParams.get("radius")).toBe("30");
  expect(mask.searchParams.get("seconds")).toBe("11");
  expect(material.VideoTrackClips[0].MaskVideoURL).toBeUndefined();
  expect(border.VideoTrackClips[0].Type).toBe("Image");
  expect(timeline.SubtitleTracks[1].SubtitleTrackClips[0].Y).toBe(0.62);
  expect(playerTimeline(timeline).VideoTracks.length).toBeGreaterThan(3);
  expect(previewTransitions({ ...draft, tracks: [...draft.tracks, { id: "transition", target: "transition", duration: 1, transition_boundary: 1, editor: draft.tracks[0].editor }] }, [0, 0]).draft.tracks.some((track) => track.target === "transition")).toBe(false);
  draft.split_screen!.enabled = false;
  expect(buildTimeline(draft, [], sampleMedia, resolved).VideoTracks).toHaveLength(1);
});

// 与 Python 同一组输入的几何锚点，缩进后的圆角和边框仍围住实际画面。
test("完整显示采用可见图像矩形，裁剪填满采用配置矩形", () => {
  const window = { ...defaultSplitScreen().material, x: 10, y: 10, width: 80, height: 50, radius: 24, borderWidth: 6 };
  expect(screenRect(window, { width: 1920, height: 1080 })).toEqual({ x: 108, y: 429, width: 864, height: 486, radius: 24, border: 6 });
  expect(screenRect({ ...window, fit: "Cover" }, { width: 1920, height: 1080 })).toEqual({ x: 108, y: 192, width: 864, height: 960, radius: 24, border: 6 });
});

// 关闭同屏不会清除已填写参数；两个窗口的输入与字幕独立，范围约束给浏览器表单使用。
test("编辑器独立配置两个窗口并保留关闭前的值", () => {
  function Editor() {
    const [layout, setLayout] = useState(defaultSplitScreen());
    return <SplitScreenEditor value={layout} onChange={setLayout} />;
  }
  render(<Editor />);
  fireEvent.click(screen.getByText("启用同屏"));
  fireEvent.change(screen.getByLabelText("素材圆角 px"), { target: { value: "20" } });
  expect(screen.getByLabelText<HTMLInputElement>("数字人圆角 px").value).toBe("30");
  fireEvent.click(screen.getByText("启用同屏"));
  fireEvent.click(screen.getByText("启用同屏"));
  expect(screen.getByLabelText<HTMLInputElement>("素材圆角 px").value).toBe("20");
  expect(screen.getByLabelText<HTMLInputElement>("素材宽度 %").max).toBe("92");
});

// 降级预览的选择与 SDK 一致，定位使用同一个绝对时钟；不以模拟 DOM 宣称已解码视频。
test("基础预览展示同屏角色并跟随定位", () => {
  const ref = createRef<PlaybackHandle>();
  const layout = { ...defaultSplitScreen(), enabled: true, backgroundColor: "#224466" };
  const resolved = resolveTemplate(newDraft(), previewContext(sampleMedia));
  render(<BasicVideoPreview ref={ref} media={sampleMedia} resolved={resolved} order={[0, 1, 2]} layout={layout} avatarSource={1}
    onTime={() => {}} onReady={() => {}} onError={() => {}} onPlayingChange={() => {}} onEnd={() => {}} />);
  const avatar = screen.getByLabelText<HTMLVideoElement>("数字人同屏预览");
  expect(avatar.getAttribute("src")).toBe(sampleMedia[1].url);
  act(() => ref.current!.seek(2));
  expect(avatar.currentTime).toBe(2);
  expect(screen.getByLabelText("素材同屏预览").getAttribute("src")).toBe(sampleMedia[0].url);
});
