/** 将模板草稿转换为 SDK 5.2.2 母版、文字和特效轨道，并生成时间轴展示数据。 */
import type { ClipTransform, ContentBox } from "@/features/sprites/parameters";
import {
  effectGroups,
  textKey,
  type Draft,
  type Editor,
  type EffectAsset,
  type EffectKey,
  type TextRole,
  type TextLanguage,
  type PreviewMedia,
} from "./model";
import type { TimelineAction, TimelineRow } from "@xzdarcy/timeline-engine";
import { effectTargets, isTextTarget, type EffectTarget } from "./effects";
import { trackLabel, trackLayers, type ResolvedTimeline } from "./tracks";
import { previewVideos } from "./media";
import type { ClipSlot } from "./clips";
import { previewFont } from "./fonts";
import { FRAME_MASK_OBJECT_ID, frameMaskImage, frameMaskRects } from "./frameMask";
import { previewRect, borderSvg, previewMaterial, previewMaskUrl, type ScreenPoint } from "./splitScreen";
import { defaultAvatarEffects, validateAvatarEffects } from "./avatarEffects";
import { previewHeadCircle, previewOpeningDuration, previewCircleMaskUrl } from "./avatarPreview";

/** 预览轨道保留源素材范围，缩略图采样使用源时间，游标使用预览时间。 */
export interface PreviewClip extends TimelineAction {
  label: string;
  effectName?: string;
  url: string;
  sourceIn: number;
  sourceOut: number;
  targetId?: string;
  /** 视频来源编号在排列变化后保持不变。 */
  videoSource?: number;
}

/** 轨道从 buildTimeline 的结果派生（预览按当前草稿生成，不等 SDK 载入）；每个视频独立成行，效果按图层排列。 */
export function buildPreviewRows(timeline: ReturnType<typeof buildTimeline>): (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[] {
  const rows: (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[] = [...timeline.previewRows, ...timeline.previewVideoTracks.map((track, trackIndex) => ({
    id: `video-${trackIndex}`,
    actions: track.VideoTrackClips.map((clip) => {
      return {
          id: `video-${clip.source}`,
          effectId: "video",
          start: clip.TimelineIn,
          end: clip.TimelineOut,
          movable: false,
          flexible: false,
          label: `视频 ${clip.source + 1}`,
          videoSource: clip.source,
          url: clip.MediaURL,
          sourceIn: clip.In,
          sourceOut: clip.Out,
      };
    }),
  }))];
  if (timeline.frameMask) rows.push({ id: FRAME_MASK_OBJECT_ID, actions: [{
    id: FRAME_MASK_OBJECT_ID, targetId: FRAME_MASK_OBJECT_ID, effectId: "frame-mask",
    start: 0, end: timeline.duration, movable: false, flexible: false,
    label: "上下蒙版", effectName: frameMaskRects(timeline.frameMask, timeline.FECanvas.Height).length ? "上下蒙版" : "上下蒙版（未显示）",
    url: "", sourceIn: 0, sourceOut: 0,
  }] });
  if (timeline.openingDuration > 0) rows.push({ id: "avatar-mask", actions: [{
    id: "avatar-mask", targetId: "avatar-mask", effectId: "avatar-mask",
    start: 0, end: timeline.openingDuration, movable: false, flexible: false,
    label: "圆形蒙版", effectName: "圆形开场", url: "", sourceIn: 0, sourceOut: 0,
  }] });
  const effects = rows.filter((row) => row.actions[0]?.effectId !== "video");
  const videos = rows.filter((row) => row.actions[0]?.effectId === "video").flatMap((row) => row.actions);
  const videoRows = videos.map((clip) => ({ id: clip.id, actions: [clip] }));
  return groupTransitionRows([...effects, ...videoRows]);
}

/** 全部可见转场共用一行，每个条目保留独立的选中 ID。 */
function groupTransitionRows(rows: (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[]) {
  const actions = rows.filter((row) => row.actions[0]?.effectId === "transition").flatMap((row) => row.actions);
  const result = rows.filter((row) => row.id !== "transitions" && row.actions[0]?.effectId !== "transition");
  result.push({ id: "transitions", actions: actions.sort((a, b) => a.start - b.start) });
  // 蒙版固定在文字/特效行下、视频行上；转场仍沿用原来的共用行。
  const layer = (row: (typeof rows)[number]) => row.id === "transitions" ? trackLayers.transition : row.id === FRAME_MASK_OBJECT_ID || row.id === "avatar-mask" ? 100 : row.actions[0]?.effectId === "video" ? Infinity : trackLayers[row.actions[0]?.effectId as EffectTarget] ?? 99;
  return result.sort((a, b) => layer(a) - layer(b));
}

/** 时间轴上的 AI 资产片段：固定长度，按所占对象放进对应的行。 */
export interface SpriteClip {
  id: string;
  spriteId: string;
  /** 目录里的来源版本：覆盖资产后刷新播放器，不写入模板轨道。 */
  sourceVersionId?: string;
  aspect: number;
  name: string;
  start: number;
  end: number;
  /** 这次摆放的完整时长（秒）：Sprite 按它排布与播放，end 只是被预览视频截短后的显示终点。 */
  length: number;
  /** 占用的模板对象；字效尚未选择文字对象或资产不在目录中时为空，单独成行提示。 */
  slot?: ClipSlot;
  textTrackId?: string;
  /** 转场所在的视频交界处。 */
  boundary?: number;
  /** 这次摆放的参数覆盖（顶层参数），播放器合并到资产默认值之上。 */
  values?: Record<string, unknown>;
  /** 宿主变换，未设置的字段取默认值。 */
  transform?: ClipTransform;
  /** 同屏字幕在圆形开场内沿用普通摆放；只用于预览，不写入模板。 */
  opening?: { end: number; transform?: ClipTransform };
  /** 资产画布与内容区域，用于在预览上拖动位置；movable 为 false 的分类只能调整强度。 */
  canvas?: { width: number; height: number; box: ContentBox; movable: boolean };
  /** 双语字效（双语字幕）：它同时显示中英两种语言，所作用对象的英文文字也不交给 IMS。 */
  bilingual?: boolean;
}

/**
 * 作用于文字的 Remotion 字效所替换的 IMS 文字对象；`bilingual` 里的片段是双语字效，它同时替换该对象的英文文字
 * （上栏中文、下栏英文都在同一个字效里）。
 */
export function remotionTexts(draft: Pick<Draft, "clips">, bilingual?: ReadonlySet<string>): Set<string> {
  return new Set(draft.clips.flatMap((clip) => clip.text_target
    ? [textKey(clip.text_target, clip.language), ...(bilingual?.has(clip.id) ? [textKey(clip.text_target, "en")] : [])]
    : []));
}

/**
 * 把 Remotion 片段并入 IMS 对象的行，与 IMS 对象一样按所占的对象放行：字效在所作用的标题或字幕行上（该行的条属于字效，
 * 点击选中字效）；全部转场共用一行，画面特效与滤镜放进同类对象的行里，时间重叠时才另起一行（滤镜可多个）；
 * 未确定对象的片段排在视频之前、各占一行提示。
 */
export function mergeSpriteRows(rows: ReturnType<typeof buildPreviewRows>, clips: SpriteClip[]): ReturnType<typeof buildPreviewRows> {
  const result = rows.map((row) => {
    const text = clips.find((clip) => clip.textTrackId ? clip.textTrackId === row.id : clip.slot === row.actions[0]?.effectId && isTextTarget(clip.slot));
    return text ? { ...row, actions: row.actions.map((action) => ({ ...action, id: text.id, targetId: text.id, effectName: `Remotion · ${text.name}` })) } : { ...row, actions: [...row.actions] };
  });
  for (const clip of clips.filter((item) => !item.slot || !isTextTarget(item.slot))) {
    const action = {
      id: clip.id, effectId: clip.slot ?? "remotion", start: clip.start, end: clip.end, movable: false, flexible: false,
      // 读回的转场片段可能还没有选择交界处，此时只显示对象名称。
      label: clip.slot === "transition" ? `镜头转场 ${clip.boundary ?? ""}`.trim() : clip.slot ? effectTargets[clip.slot] : "AI 资产",
      effectName: `Remotion · ${clip.name}`, url: "", sourceIn: 0, sourceOut: 0, targetId: clip.id,
    };
    const home = clip.slot && result.find((row) => row.actions[0]?.effectId === clip.slot && row.actions.every((other) => other.end <= action.start || action.end <= other.start));
    if (home) home.actions.push(action);
    else result.push({ id: `row-${clip.id}`, actions: [action] });
  }
  return groupTransitionRows(result);
}

/** 每秒约取一张源素材缩略图，使用区间中心，避免在片段结束边界采样。 */
export function thumbnailTimes(clip: Pick<PreviewClip, "sourceIn" | "sourceOut">): number[] {
  const { sourceIn, sourceOut } = clip;
  if (!Number.isFinite(sourceIn) || !Number.isFinite(sourceOut) || sourceIn < 0 || sourceOut <= sourceIn)
    throw new Error("视频片段的源时间范围无效");
  const count = Math.min(20, Math.ceil(sourceOut - sourceIn));
  return Array.from({ length: count }, (_, index) => sourceIn + (index + 0.5) * (sourceOut - sourceIn) / count);
}

/** SDK 效果允许混合文字、数值参数；仅从可信目录和经过范围校验的配置生成。 */
type Effect = Record<string, string | number>;
/** SDK 视频轨兼容图片装饰；业务时间轴另取原来的三个示例片段。 */
interface PreviewVideo { MaskVideoURL?: string; source?: number; Type: string; MediaURL: string; In?: number; Out?: number; Duration?: number; TimelineIn: number; TimelineOut: number; X?: number; Y?: number; Width: number; Height: number; AdaptMode: string; Effects: Effect[] }

/**
 * 模板预览的画布：固定为成片的 1080×1920（服务端 composition_width/height 的默认值），与 AI 资产画布一致，
 * 不随用户换的预览视频变化；字号与位置都按这个画布计算。
 */
export const PREVIEW_CANVAS = { width: 1080, height: 1920 } as const;

/**
 * 与正式合成（video_composition/timeline.py）一致：视频按 Contain 等比完整放入，留白用模糊背景填充。
 * 比例与画布相同（如 720×1280）时没有留白，模糊层会被完全盖住，所以不加，省去整幅模糊的绘制开销。
 */
function letterbox(video: { width: number; height: number }): Effect[] {
  const fits = Math.abs(video.width / video.height - PREVIEW_CANVAS.width / PREVIEW_CANVAS.height) < 0.005;
  return fits ? [] : [{ Type: "Background", SubType: "Blur", Radius: 0.1 }];
}

/** 预览示例文字中强调的关键词：标题优先使用指定关键词，否则取首段前两个字；字幕取第一个词语。合成时改用切片结果。 */
export function exampleKeyword(config: Editor, role: "title" | "subtitle", language?: TextLanguage): string {
  if (language === "en") return (role === "title" ? config.titleKeyword : "") || [...new Intl.Segmenter("en", { granularity: "word" }).segment(config[role])].find((part) => part.isWordLike)?.segment || "";
  return role === "title"
    ? config.titleKeyword || (config.title.match(/[\p{L}\p{N}]{1,2}/u)?.[0] ?? "")
    : [...new Intl.Segmenter("zh", { granularity: "word" }).segment(config.subtitle)].find((part) => part.isWordLike)?.segment ?? "";
}

/** 校验数值输入，不让空值、NaN 或越界参数进入 SDK。 */
function number(
  value: number,
  min: number,
  max: number,
  label: string,
  integer = false,
): number {
  if (
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (integer && !Number.isInteger(value))
  ) {
    throw new Error(`${label}须为 ${min}～${max}${integer ? " 的整数" : ""}`);
  }
  return value;
}

/** 从单个对象参数生成文字或效果，校验目录引用及数值范围。 */
function buildTrackContent(config: Editor, catalog: EffectAsset[], target: EffectTarget, language?: TextLanguage) {
  const byId = new Map(catalog.map((item) => [item.id, item]));
  // 效果类别与字段绑定，拒绝错误类别及未知目录条目。
  const effect = (key: EffectKey): Effect => {
    if (!config[key]) return {};
    const item = byId.get(config[key]);
    if (!item || item.category !== effectGroups[key])
      throw new Error(`效果不在对应目录中：${config[key]}`);
    return { ...item.parameters };
  };
  // SDK 小于 1 才识别为相对坐标，100% 需要限制在边界内。
  const position = (value: number) =>
    Math.min(number(value, 0, 100, "位置") / 100, 0.9999);
  const text = (role: TextRole) => {
    const content = role === "bubble" ? config.bubbleText : config[role];
    const size = number(config[`${role}Size`], 12, 300, "字号", true);
    const previewKeyword = role === "bubble" ? "" : exampleKeyword(config, role, language);
    if (
      config[`${role}Loop`] &&
      (config[`${role}In`] || config[`${role}Out`])
    ) {
      throw new Error("同一类文字的循环动画不能与入场、出场同时使用");
    }
    const motion: Effect = {
      ...effect(`${role}In`),
      ...effect(`${role}Out`),
      ...effect(`${role}Loop`),
    };
    for (const type of ["In", "Out"] as const) {
      const duration = number(
        config[`${role}${type}Duration`],
        1 / 30,
        3,
        "动画时长",
      );
      if (config[`${role}${type}`]) motion[`AaiMotion${type}`] = duration;
    }
    return {
      Type: "Text",
      Content: role === "title" || role === "subtitle" ? formatKeyword(content, previewKeyword, config, role) : content,
      TimelineIn: 0,
      TimelineOut: 10,
      X: position(config[`${role}X`]),
      Y: position(config[`${role}Y`]),
      Alignment: "Center",
      Font: role === "bubble" ? "Alibaba PuHuiTi" : previewFont(config[`${role}Font`]),
      FontSize: size,
      FontColor: "#FFFFFF",
      Outline: 0,
      // 与合成端相同的有界换行；SDK 未指定文字宽度时会把居中坐标导出为 NaN。
      ...(role !== "bubble" ? { AdaptMode: "AutoWrap", TextWidth: 1 } : {}),
      ...effect(role === "bubble" ? "bubble" : `${role}Flower`),
      ...motion,
      ...(role === "bubble" ? { Width: 0.5 } : {}),
      ...(language === "en" ? { AdaptMode: "AutoScale", TextWidth: 0.9 } : {}),
    };
  };
  if (!isTextTarget(target) && !config[target]) throw new Error("特效轨道缺少效果");
  return {
    text: isTextTarget(target) ? text(target) : undefined,
    effect: isTextTarget(target) ? undefined : {
      Type: target === "transition" ? "Transition" : target === "filter" ? "Filter" : "VFX",
      ...effect(target),
    } as Effect,
  };
}

/** 母版、文字和全画面效果从同一份实例数据生成，SDK 自动转换后端 Timeline 格式。 */
export function buildTimeline(draft: Draft, catalog: EffectAsset[], media: PreviewMedia, resolvedTimeline: ResolvedTimeline, order = [0, 1, 2], avatarSource = 0, previewCenter?: ScreenPoint, bilingual?: ReadonlySet<string>) {
  const previewRows: (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[] = [];
  const EffectTracks: { EffectTrackItems: Effect[] }[] = [];
  const transitions: { boundary: number; start: number; end: number; effect: Effect }[] = [];
  const notices = resolvedTimeline.notices;
  if ("editor" in draft || !Array.isArray(draft.tracks)) throw new Error("模板格式不支持，请重新创建模板");
  const canvas = { Width: PREVIEW_CANVAS.width, Height: PREVIEW_CANVAS.height };
  const sources = previewVideos(media);
  const avatarEffects = draft.avatar_effects ?? defaultAvatarEffects();
  validateAvatarEffects(avatarEffects);
  const openingDuration = previewOpeningDuration(avatarEffects, resolvedTimeline.duration);
  const ranges = resolvedTimeline.clips.map((range, index) => ({ ...range, source: order[index], video: sources[order[index]] }));
  const timeline = {
    VideoTracks: [{ VideoTrackClips: ranges.map(({ source, video, start, end }) => ({
      source, Type: "Video", MediaURL: video.url,
      In: 0, Out: video.duration, TimelineIn: start, TimelineOut: end,
      Width: 0.9999, Height: 0.9999, AdaptMode: "Contain",
      Effects: [{ Type: "Volume", Gain: 0 }, ...letterbox(video)] as Effect[],
    })) }],
    SubtitleTracks: [] as { SubtitleTrackClips: NonNullable<ReturnType<typeof buildTrackContent>["text"]>[] }[],
    AudioTracks: [], AspectRatio: `${canvas.Width}:${canvas.Height}`, FECanvas: canvas,
  };
  const previewVideoTracks = timeline.VideoTracks;
  const layout = draft.split_screen?.enabled ? draft.split_screen : undefined;
  const splitTracks: { VideoTrackClips: PreviewVideo[] }[] = [];
  if (layout) {
    const bg = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920"><path fill="${layout.backgroundColor}" d="M0 0h1080v1920H0z"/></svg>`)}`;
    if (layout.layout === "split") splitTracks.push({ VideoTrackClips: [{ Type: "Image", MediaURL: bg, Duration: resolvedTimeline.duration, TimelineIn: 0, TimelineOut: resolvedTimeline.duration, Width: 0.9999, Height: 0.9999, AdaptMode: "Fill", Effects: [] }] });
    for (const role of ["material", "avatar"] as const) {
      const clips: PreviewVideo[] = [], borders: PreviewVideo[] = [];
      for (const range of ranges) {
        const video = sources[role === "avatar" ? avatarSource : previewMaterial(range.source, avatarSource)];
        const matting = role === "avatar" && layout.layout !== "circle" && avatarEffects.removeBackground;
        const rect = previewRect(layout, role, video);
        if (matting) { rect.radius = 0; rect.border = 0; }
        const circle = role === "avatar" && layout.layout === "circle" ? previewHeadCircle(video, avatarEffects.circleScale, previewCenter) : undefined;
        const fullMaterial = role === "material" && layout.layout !== "split";
        const geometry = { X: rect.x / 1080, Y: rect.y / 1920, Width: Math.min(rect.width / 1080, 0.9999), Height: Math.min(rect.height / 1920, 0.9999), AdaptMode: fullMaterial ? "Contain" : "Cover" };
        const effects: Effect[] = [{ Type: "Volume", Gain: 0 }, ...(fullMaterial ? letterbox(video) : [])];
        if (circle) effects.push({ Type: "Crop", X: (circle.x - circle.radius) / video.width, Y: (circle.y - circle.radius) / video.height,
          Width: circle.radius * 2 / video.width, Height: circle.radius * 2 / video.height });
        if (matting) effects.push({ Type: "AI_RealMatting" });
        // 短示例在预览内循环；正式合成的数字人始终使用口播绝对源时间。
        let at = Math.max(range.start, openingDuration);
        while (at < range.end - 1e-8) {
          let offset = role === "avatar" ? at % video.duration : (at - range.start) % video.duration;
          if (video.duration - offset < 1e-7) offset = 0;
          const end = Math.min(range.end, at + video.duration - offset);
          clips.push({ ...(circle ? { MaskVideoURL: previewCircleMaskUrl(video, circle, avatarEffects.feather) } : rect.radius ? { MaskVideoURL: previewMaskUrl(rect, video) } : {}), Type: "Video", MediaURL: video.url, In: offset, Out: offset + end - at, TimelineIn: at, TimelineOut: end, ...geometry, Effects: effects });
          at = end;
        }
        if (rect.border) borders.push({ Type: "Image", MediaURL: borderSvg(rect, layout[role].borderColor), Duration: range.end - range.start, TimelineIn: range.start, TimelineOut: range.end, ...geometry, Effects: [] });
      }
      splitTracks.push({ VideoTrackClips: clips });
      if (borders.length) splitTracks.push({ VideoTrackClips: borders });
    }
  }
  // 开场叠在业务视频和转场之上，结束后直接显露原进度；不改变总时长。
  const openingTracks: { VideoTrackClips: PreviewVideo[] }[] = [];
  if (openingDuration > 0) {
    const video = sources[avatarSource], circle = previewHeadCircle(video, avatarEffects.circleScale, previewCenter);
    const black = `data:image/svg+xml;charset=utf-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920"><path fill="black" d="M0 0h1080v1920H0z"/></svg>')}`;
    openingTracks.push({ VideoTrackClips: [{ Type: "Image", MediaURL: black, Duration: openingDuration, TimelineIn: 0, TimelineOut: openingDuration, Width: 1, Height: 1, AdaptMode: "Fill", Effects: [] }] });
    const clips: PreviewVideo[] = [];
    for (let at = 0; at < openingDuration - 1e-8; at += video.duration) {
      const length = Math.min(video.duration, openingDuration - at);
      clips.push({ Type: "Video", MediaURL: video.url, MaskVideoURL: previewCircleMaskUrl(video, circle, avatarEffects.feather),
        In: 0, Out: length, TimelineIn: at, TimelineOut: at + length, Width: 0.9999, Height: 0.9999, AdaptMode: "Contain", Effects: [{ Type: "Volume", Gain: 0 }] });
    }
    openingTracks.push({ VideoTrackClips: clips });
  }
  const seen = new Set<string>();
  // 改由 Remotion 字效显示的文字仍保留时间轴行，但不交给 SDK 渲染；双语字效连它所作用对象的英文文字一起替换。
  const replaced = remotionTexts(draft, bilingual);
  for (const source of [...draft.tracks].sort((a, b) => trackLayers[a.target] - trackLayers[b.target])) {
    if (source.enabled === false || (layout && source.target === "transition")) continue;
    const applied = resolvedTimeline.tracks[source.id];
    if (!applied) throw new Error("对象缺少时间计算结果");
    const track = { ...source, ...applied };
    if (!track.id || seen.has(track.id)) throw new Error("特效轨道 ID 必须唯一");
    seen.add(track.id);
    const resolved = buildTrackContent(track.editor, catalog, track.target, track.language);
    if (track.end <= track.start) continue;
    if (isTextTarget(track.target)) {
      const point = layout && track.target === "subtitle" ? layout[track.language === "en" ? "subtitleEn" : "subtitle"] : undefined;
      const text = resolved.text && point ? { ...resolved.text, X: Math.min(point.x / 100, 0.9999), Y: Math.min(point.y / 100, 0.9999) } : resolved.text;
      if (!text?.Content.trim()) throw new Error("文字轨道内容不能为空");
      if (!replaced.has(textKey(track.target, track.language))) {
        const intervals = point && openingDuration > track.start ? [
          { ...resolved.text!, TimelineIn: track.start, TimelineOut: Math.min(track.end, openingDuration) },
          ...(track.end > openingDuration ? [{ ...text, TimelineIn: openingDuration, TimelineOut: track.end }] : []),
        ] : [{ ...text, TimelineIn: track.start, TimelineOut: track.end }];
        // 摆放位置切换不是新的字幕，内部边界不重播入出场动画。
        if (intervals.length === 2) {
          Reflect.deleteProperty(intervals[0], "AaiMotionOutEffect");
          Reflect.deleteProperty(intervals[1], "AaiMotionInEffect");
        }
        timeline.SubtitleTracks.push({ SubtitleTrackClips: intervals });
      }
    } else if (track.target === "transition") {
      const effect = resolved.effect;
      if (!effect) throw new Error("转场轨道缺少效果");
      effect.Duration = Math.round((track.end - track.start) * 30) / 30;
      transitions.push({ boundary: source.transition_boundary!, start: track.start, end: track.end, effect });
    } else {
      const effect = resolved.effect;
      if (!effect) throw new Error("特效轨道缺少效果");
      EffectTracks.push({ EffectTrackItems: [{ ...effect, TimelineIn: track.start, TimelineOut: track.end }] });
    }
    const effectId = { title: track.editor.titleFlower, subtitle: track.editor.subtitleFlower, bubble: track.editor.bubble, filter: track.editor.filter, vfx: track.editor.vfx, transition: track.editor.transition }[track.target];
    const effectName = catalog.find((item) => item.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : undefined);
    previewRows.push({ id: track.id, actions: [{
      id: track.id, targetId: track.id, effectId: track.target,
      start: track.start, end: track.end, movable: false, flexible: false,
      label: trackLabel(track, draft.tracks), effectName, url: "", sourceIn: 0, sourceOut: 0,
    }] });
  }
  return { ...timeline, VideoTracks: layout ? splitTracks : timeline.VideoTracks as { VideoTrackClips: PreviewVideo[] }[], openingTracks, previewVideoTracks, EffectTracks, previewRows, notices, transitions, openingDuration, frameMask: draft.frame_mask, duration: resolvedTimeline.duration };
}

/** SDK 将专用轨道追加到视频之后；统一传入从下到上的图层，保证文字位于滤镜和 VFX 上方。 */
export function playerTimeline(timeline: ReturnType<typeof buildTimeline>, transitionFrames: ReadonlyMap<string, string> = new Map()) {
  const mask = frameMaskImage(timeline.frameMask, timeline.FECanvas.Width, timeline.FECanvas.Height);
  const videos = timeline.VideoTracks[0].VideoTrackClips;
  const transitionTracks = timeline.transitions.map(({ boundary, start, end, effect }) => ({
    VideoTrackClips: [videos[boundary - 1], videos[boundary]].map((video, index) => {
      const key = transitionFrameKey(boundary, index);
      const image = transitionFrames.get(key);
      if (!image) throw new Error("居中转场缺少视频补帧画面");
      return {
        Type: "Image", MediaURL: image, Duration: end - start, TimelineIn: start, TimelineOut: end,
        Width: video.Width, Height: video.Height, AdaptMode: video.AdaptMode,
        // 补帧画面与原视频比例相同，留白沿用视频片段的模糊背景，没有时用黑色盖住下层。
        Effects: [video.Effects.find((item) => item.Type === "Background") ?? { Type: "Background", SubType: "Color", Color: "#000000" }, ...(index === 0 ? [effect] : [])],
      };
    }),
  }));
  return {
    AspectRatio: timeline.AspectRatio, FECanvas: timeline.FECanvas, AudioTracks: timeline.AudioTracks,
    VideoTracks: [
      ...timeline.VideoTracks.map((row) => ({ VideoTrackClips: row.VideoTrackClips.map(({ source: _source, ...clip }) => clip) })),
      ...transitionTracks,
      ...timeline.openingTracks,
      ...(mask ? [{ VideoTrackClips: [{ Type: "Image", MediaURL: mask, TimelineIn: 0, TimelineOut: timeline.duration,
        // SDK 5.2.2 将小于 2 的图片尺寸按画布比例换算；1 精确铺满，0.9999 会在右侧和底部留下黑边。
        // IMS 服务端仍使用实际像素宽高，不能把这里的 SDK 比例原样发给云端。
        Duration: timeline.duration, X: 0, Y: 0, Width: 1, Height: 1, AdaptMode: "Fill" }] }] : []),
      ...[...timeline.EffectTracks].reverse().map((row) => ({ Type: "Effect", VideoTrackClips: row.EffectTrackItems })),
      ...[...timeline.SubtitleTracks].reverse().map((row) => ({ Type: "Subtitle", VideoTrackClips: row.SubtitleTrackClips })),
    ],
  };
}

/** 补帧键仅在这次预览内标识前后画面，媒体缓存使用地址和源时间。 */
export function transitionFrameKey(boundary: number, index: number): string {
  return `${boundary}:${index}`;
}

/** 将启用的局部样式包在首次出现的关键词两侧；无关键词时保留原文字。 */
export function formatSubtitleKeyword(content: string, keyword: string, config: Editor): string {
  return formatKeyword(content, keyword, config, "subtitle");
}

/** 生成标题或字幕的局部样式；合成标题没有匹配词语时保留完整标题。 */
export function formatKeyword(content: string, keyword: string, config: Editor, role: "title" | "subtitle"): string {
  const color = config[`${role}KeywordColor`];
  const size = config[`${role}KeywordSize`];
  if (color && !/^#[0-9A-Fa-f]{6}$/.test(color)) throw new Error("关键词颜色须为 #RRGGBB");
  if (size !== 0 && (!Number.isInteger(size) || size < 12 || size > 300)) throw new Error("关键词字号须为 12～300 的整数");
  const styles = [
    [config[`${role}KeywordBold`], "\\b1", "\\b0"],
    [config[`${role}KeywordItalic`], "\\i1", "\\i0"],
    [config[`${role}KeywordUnderline`], "\\u1", "\\u0"],
    [config[`${role}KeywordStrikeout`], "\\s1", "\\s0"],
  ] as const;
  const enabled = styles.filter(([selected]) => selected);
  if (!keyword || (!enabled.length && !color && !size)) return content;
  const start = content.indexOf(keyword);
  if (start < 0) {
    if (role === "title") return content;
    throw new Error("关键词不在字幕文字中");
  }
  const bgr = color ? `${color.slice(5, 7)}${color.slice(3, 5)}${color.slice(1, 3)}`.toUpperCase() : "";
  const opening = (color ? `\\1c&${bgr}&` : "") + (size ? `\\fs${size}` : "") + enabled.map(([, open]) => open).join("");
  const closing = (color ? "\\1c" : "") + (size ? "\\fs" : "") + enabled.map(([, , close]) => close).join("");
  return `${content.slice(0, start)}{${opening}}${keyword}{${closing}}${content.slice(start + keyword.length)}`;
}
