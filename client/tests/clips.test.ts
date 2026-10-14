/** Remotion 片段编辑操作的核心测试：添加、所占模板对象与共用名额、移动、移除、参数覆盖、宿主变换与共用轨道上限，以及发布参数到控件的转换；纯函数，不依赖界面。 */
import { expect, test } from "bun:test";
import { create } from "@bufbuild/protobuf";
import { OperatorAccess, SpriteKind, SpriteParameterFormat, SpriteParameterSchema } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { assetParameter, categoryOf, transformFields } from "@/features/sprites/parameters";
import { validParameter } from "@/components/ParameterControl";
import { addClip, bindClip, hiddenTextClips, moveClip, removeClip, setClipParameter, setClipTransform, timeClips, validateClipSlots } from "@/features/templates/clips";
import { addTrack, previewContext, resolveTemplate, setTrackTiming } from "@/features/templates/tracks";
import type { Draft } from "@/features/templates/model";
import { catalog, sampleDraft, sampleMedia } from "./fixtures";

// 滤镜不占用模板对象，可以重复添加；占用对象的分类在下方单独覆盖。
const asset: SpriteAsset = { id: "sprite-1", name: "暖色滤镜", kind: SpriteKind.FILTER_OVERLAY, seconds: 3, aspect: 9 / 16, width: 1080, height: 1920, fps: 30, box: { x: 0, y: 0, width: 1080, height: 1920 }, parameters: [] };

// 场景：添加的片段从 0 秒开始、长度为资产自身时长；同一资产可以重复添加，每次是独立摆放；输入草稿和 IMS 对象引用都不变。
test("添加片段不改动输入，同一资产可重复添加", () => {
  const draft = sampleDraft();
  const once = addClip(draft, asset, []);
  const twice = addClip(once, asset, [asset]);
  expect(draft.clips).toEqual([]);
  expect(twice.clips).toHaveLength(2);
  expect(twice.clips.map((clip) => clip.sprite_id)).toEqual(["sprite-1", "sprite-1"]);
  expect(twice.clips[0]).toMatchObject({ start: 0, duration: 3 });
  expect(twice.clips[0].id).not.toBe(twice.clips[1].id);
  expect(twice.tracks).toBe(draft.tracks);
});

// 场景：目录条目没有可用时长时不写入 0 秒，而是留空，由服务端沿用资产自身时长。
test("资产时长无效时片段时长留空", () => {
  expect(addClip(sampleDraft(), { ...asset, seconds: 0 }, []).clips[0].duration).toBeNull();
});

// 场景：移动只改起点（毫秒精度），时长和其他片段不变；未知 ID 不产生任何变化。
test("移动片段只改起点", () => {
  const draft = addClip(addClip(sampleDraft(), asset, []), { ...asset, id: "sprite-2" }, [asset]);
  const [first, second] = draft.clips;
  const moved = moveClip(draft, first.id, 1.23456);
  expect(moved.clips[0]).toEqual({ ...first, start: 1.235 });
  expect(moved.clips[1]).toBe(second);
  expect(moveClip(draft, "missing", 5).clips).toEqual(draft.clips);
});

// 场景：移除只去掉指定片段，其余片段的先后（叠放层级）保持。
test("移除片段保持其余顺序", () => {
  const draft = [asset, { ...asset, id: "sprite-2" }, { ...asset, id: "sprite-3" }].reduce((current, item, _, all) => addClip(current, item, all), sampleDraft());
  const removed = removeClip(draft, draft.clips[1].id);
  expect(removed.clips.map((clip) => clip.sprite_id)).toEqual(["sprite-1", "sprite-3"]);
  expect(draft.clips).toHaveLength(3);
});

// 场景：片段与 IMS 对象共用 100 条上限，占满后不能再添加片段。
test("片段与对象共用轨道上限", () => {
  const draft = sampleDraft();
  draft.clips = Array.from({ length: 98 }, (_, index) => ({ id: `clip-${index}`, sprite_id: "sprite", start: 0, duration: null, parameters: {}, transform: {} }));
  expect(() => addClip(draft, asset, [])).toThrow("轨道数量不能超过 100");
  draft.clips.pop();
  expect(addClip(draft, asset, []).clips).toHaveLength(98);
});

// 场景：叠加渲染要求同一模板里的资产画布一致：与已添加片段宽、高或帧率不同的资产被拒绝并说明两边画布，相同画布的不同资产可以添加；目录里没有已添加资产时不阻止（服务端会复核）。
test("画布不一致的资产不能加入同一模板", () => {
  const first = addClip(sampleDraft(), asset, []);
  for (const other of [{ width: 1920, height: 1080 }, { fps: 60 }]) {
    const different = { ...asset, id: "sprite-x", name: "横版", ...other };
    expect(() => addClip(first, different, [asset])).toThrow(/不一致/);
  }
  expect(() => addClip(first, { ...asset, id: "sprite-x", fps: 60 }, [asset])).toThrow("1080×1920 @ 60 FPS");
  expect(addClip(first, { ...asset, id: "sprite-y" }, [asset]).clips).toHaveLength(2);
  expect(addClip(first, { ...asset, id: "sprite-z", fps: 60 }, []).clips).toHaveLength(2);
});

// 场景：字效作用于当前选择的标题或字幕，同一文字对象只能有一个，气泡不能使用 Remotion 字效。
test("字效作用于所选文字对象且每个对象最多一个", () => {
  const text = { ...asset, id: "text", kind: SpriteKind.TEXT };
  const title = addClip(sampleDraft(), text, [text], "title");
  expect(title.clips[0].text_target).toBe("title");
  expect(() => addClip(title, text, [text], "title")).toThrow("已有 Remotion 字效");
  expect(addClip(title, text, [text], "subtitle").clips.map((clip) => clip.text_target)).toEqual(["title", "subtitle"]);
  expect(() => addClip(sampleDraft(), text, [text], "bubble")).toThrow("只能作用于标题或底部字幕");
});

// 场景：Remotion 和 IMS 转场共用有序列表，支持添加超过两个候选，每个序号仅有一个效果。
test("Remotion 转场与 IMS 转场共用视频交界处", () => {
  const cut = { ...asset, id: "cut", kind: SpriteKind.TRANSITION_OVERLAY };
  const ims = addTrack(sampleDraft(), catalog[1], "transition").draft;
  const both = addClip(ims, cut, [cut]);
  expect(both.clips[0].transition_boundary).toBe(2);
  expect(addClip(both, cut, [cut]).clips.at(-1)?.transition_boundary).toBe(3);
  expect(() => addTrack(addClip(sampleDraft(), cut, [cut]), catalog[1], "transition", undefined, 1)).toThrow("交界处 1 已有 Remotion 转场");
  expect(addTrack(addClip(sampleDraft(), cut, [cut]), catalog[1], "transition").draft.tracks.at(-1)?.transition_boundary).toBe(2);
});

// 场景：Remotion 视频特效与 IMS 画面特效共用一个名额，已有任一种时拒绝再添加。
test("Remotion 视频特效占用画面特效名额", () => {
  const glow = { ...asset, id: "glow", kind: SpriteKind.VIDEO_OVERLAY };
  const once = addClip(sampleDraft(), glow, [glow]);
  expect(() => addClip(once, glow, [glow])).toThrow("画面特效最多一个");
  const ims = sampleDraft();
  ims.tracks.push({ id: "vfx", target: "vfx", start_mode: "seconds", start: 0, duration: null, editor: { ...ims.tracks[0].editor, vfx: "vfx/normal/x" } });
  expect(() => addClip(ims, glow, [glow])).toThrow("画面特效最多一个");
});

// 场景：字效改为作用于另一个文字对象时清除文字与关键词覆盖（改由该对象提供），保留其他参数；目标已被占用或保存前存在冲突时报错。
test("改变字效作用对象与转场交界处", () => {
  const text = { ...asset, id: "text", kind: SpriteKind.TEXT };
  const draft = addClip(addClip(sampleDraft(), text, [text], "title"), text, [text], "subtitle");
  const [title, subtitle] = draft.clips;
  expect(() => bindClip(draft, title.id, { text_target: "subtitle" })).toThrow("已有 Remotion 字效");
  const single = setClipParameter(setClipParameter(removeClip(draft, subtitle.id), title.id, "text", "旧文字"), title.id, "fontSize", 80);
  expect(bindClip(single, title.id, { text_target: "subtitle" }).clips[0]).toMatchObject({ text_target: "subtitle", parameters: { fontSize: 80 } });
  expect(() => validateClipSlots({ ...draft, clips: [title, { ...subtitle, text_target: "title" }] })).toThrow("最多一个 Remotion 字效");
  const cut = { ...asset, id: "cut", kind: SpriteKind.TRANSITION_OVERLAY };
  const moved = bindClip(addClip(sampleDraft(), cut, [cut]), addClip(sampleDraft(), cut, [cut]).clips[0].id, { transition_boundary: 2 });
  expect(moved.clips).toHaveLength(1);
});

// 场景：字效采用所作用文字在本次预览中的显示区间，Remotion 转场使用视频重叠区间；区间不变时返回原草稿，不改动输入。
test("片段时间跟随所占对象", () => {
  const text = { ...asset, id: "text", kind: SpriteKind.TEXT };
  const cut = { ...asset, id: "cut", kind: SpriteKind.TRANSITION_OVERLAY, seconds: 1 };
  /** 按三个 10 秒预览视频计算占用对象的片段区间。 */
  const timed = (draft: Draft) => timeClips(draft, resolveTemplate(draft, previewContext(sampleMedia)));
  expect(timed(addClip(sampleDraft(), cut, [cut])).clips[0]).toMatchObject({ transition_boundary: 1, start: 9, duration: 1 });
  let draft = setTrackTiming(sampleDraft(), "title", { start_mode: "seconds", start: 2, duration: 3 });
  // IMS 转场在第一个交界处重叠一秒，第二个交界处随之提前到 19 秒。
  draft = addClip(addClip(addTrack(draft, catalog[1], "transition").draft, text, [text, cut], "title"), cut, [text, cut]);
  const result = timed(draft);
  expect(result.clips.map(({ start, duration, transition_boundary }) => [start, duration, transition_boundary])).toEqual([[2, 3, undefined], [18, 1, 2]]);
  expect(draft.clips.map(({ start }) => start)).toEqual([0, 0]);
  expect(timed(result)).toBe(result);
  const extra = addClip(result, cut, [text, cut]);
  expect([...hiddenTextClips(extra, resolveTemplate(extra, previewContext(sampleMedia)))]).toEqual([extra.clips.at(-1)!.id]);
});

// 场景：所作用的标题在 30 秒预览里没有显示时间，字效同样不显示且保留原有区间；标题回到预览内后字效重新跟随它，滤镜不受影响。
test("所作用文字不显示时字效也不显示", () => {
  const text = { ...asset, id: "text", kind: SpriteKind.TEXT };
  const late = addClip(addClip(setTrackTiming(sampleDraft(), "title", { start_mode: "seconds", start: 40, duration: null }), text, [text], "title"), asset, [text, asset]);
  const resolved = resolveTemplate(late, previewContext(sampleMedia));
  expect([...hiddenTextClips(late, resolved)]).toEqual([late.clips[0].id]);
  expect(timeClips(late, resolved)).toBe(late);
  const shown = setTrackTiming(late, "title", { start_mode: "seconds", start: 2, duration: 3 });
  expect(hiddenTextClips(shown, resolveTemplate(shown, previewContext(sampleMedia))).size).toBe(0);
});

// 场景：参数覆盖与宿主变换逐项修改；undefined 恢复默认值，其他片段与输入草稿不变。
test("片段参数与宿主变换逐项修改与恢复", () => {
  const draft = addClip(sampleDraft(), asset, []);
  const id = draft.clips[0].id;
  const edited = setClipTransform(setClipParameter(draft, id, "text", "新品"), id, { x: 300, scale: 2 });
  expect(edited.clips[0]).toMatchObject({ parameters: { text: "新品" }, transform: { x: 300, scale: 2 } });
  const reset = setClipTransform(setClipParameter(edited, id, "text", undefined), id, { scale: undefined });
  expect(reset.clips[0]).toMatchObject({ parameters: {}, transform: { x: 300 } });
  expect(draft.clips[0]).toMatchObject({ parameters: {}, transform: {} });
});

// 场景：发布参数声明转换为控件：颜色与整数格式、关键词项数、只读与内部参数。
test("发布参数转换为控件", () => {
  const color = assetParameter(create(SpriteParameterSchema, { key: "color", label: "颜色", access: OperatorAccess.VISIBLE_EDITABLE, format: SpriteParameterFormat.COLOR, defaultValue: { value: { case: "stringValue", value: "#FFFFFF" } } }));
  expect(color).toEqual({ field: { key: "color", label: "颜色", kind: "color" }, editable: true, defaultValue: "#FFFFFF" });
  const keywords = assetParameter(create(SpriteParameterSchema, { key: "keywords", access: OperatorAccess.VISIBLE_READ_ONLY, maxItems: 3, maxLength: 4, defaultValue: { value: { case: "stringListValue", value: { values: ["a"] } } } }));
  expect(keywords).toEqual({ field: { key: "keywords", label: "keywords", kind: "keywords", maxItems: 3, maxLength: 4 }, editable: false, defaultValue: ["a"] });
  expect(assetParameter(create(SpriteParameterSchema, { key: "secret", access: OperatorAccess.INTERNAL, defaultValue: { value: { case: "numberValue", value: 1 } } }))).toBeNull();
  // number_value cannot tell an integer from a number; the format makes the control integral.
  const count = assetParameter(create(SpriteParameterSchema, { key: "particleCount", access: OperatorAccess.VISIBLE_EDITABLE, minimum: 1, maximum: 200, format: SpriteParameterFormat.INTEGER, defaultValue: { value: { case: "numberValue", value: 20 } } }));
  expect(count?.field.kind).toBe("integer");
  expect(validParameter(count!.field, 1.5)).toBe(false);
  expect(validParameter(count!.field, 2)).toBe(true);
  expect(transformFields(SpriteKind.FILTER_OVERLAY)).toEqual(["opacity"]);
  expect(categoryOf(SpriteKind.TRANSITION_OVERLAY)).toBe("effect");
});
