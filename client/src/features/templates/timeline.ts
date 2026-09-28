/** 将模板草稿转换为 SDK 5.2.2 母版、文字和特效轨道，并生成时间轴展示数据。 */
import {
  effectGroups,
  type Draft,
  type Editor,
  type EffectAsset,
  type EffectKey,
  type TextRole,
  type MasterVideo,
} from "./model";
import type { TimelineAction, TimelineRow } from "@xzdarcy/timeline-engine";
import { isTextTarget, type EffectTarget } from "./effects";
import { previewDuration, resolveTrack, trackLabel } from "./tracks";
import { previewVideoUrl } from "./media";

/** 预览轨道保留源素材范围，缩略图采样使用源时间，游标使用预览时间。 */
export interface PreviewClip extends TimelineAction {
  label: string;
  effectName?: string;
  url: string;
  sourceIn: number;
  sourceOut: number;
  transition?: { start: number; end: number };
  targetId?: string;
}

/** 轨道从已经应用的 SDK Timeline 派生；重叠片段分行展示，保持实际时间范围。 */
export function buildPreviewRows(timeline: ReturnType<typeof buildTimeline>): (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[] {
  return [...timeline.VideoTracks.flatMap((track, trackIndex) =>
    track.VideoTrackClips.map((clip, index) => {
      const next = track.VideoTrackClips[index + 1];
      const transition = clip.Effects.some((item) => item.Type === "Transition") && next
        ? { start: Math.max(clip.TimelineIn, next.TimelineIn), end: Math.min(clip.TimelineOut, next.TimelineOut) }
        : undefined;
      return {
        id: `video-${trackIndex}-${index}`,
        actions: [{
          id: `clip-${trackIndex}-${index}`,
          effectId: "video",
          start: clip.TimelineIn,
          end: clip.TimelineOut,
          movable: false,
          flexible: false,
          label: `母版视频片段 ${index + 1}`,
          url: clip.MediaURL,
          sourceIn: clip.In,
          sourceOut: clip.Out,
          transition: transition && transition.end > transition.start ? transition : undefined,
        }],
      };
    }),
  ), ...timeline.previewRows];
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
function buildTrackContent(config: Editor, catalog: EffectAsset[], target: EffectTarget) {
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
    const previewKeyword = role === "title"
      ? config.titleKeyword || (content.match(/[\p{L}\p{N}]{1,2}/u)?.[0] ?? "")
      : role === "subtitle"
        ? ([...new Intl.Segmenter("zh", { granularity: "word" }).segment(content)].find((part) => part.isWordLike)?.segment ?? "")
        : "";
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
      Font: "Alibaba PuHuiTi",
      FontSize: size,
      FontColor: "#FFFFFF",
      Outline: 0,
      ...effect(role === "bubble" ? "bubble" : `${role}Flower`),
      ...motion,
      ...(role === "bubble" ? { Width: 0.5 } : {}),
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
export function buildTimeline(draft: Draft, catalog: EffectAsset[], media?: MasterVideo) {
  const previewRows: (Omit<TimelineRow, "actions"> & { actions: PreviewClip[] })[] = [];
  const EffectTracks: { EffectTrackItems: Effect[] }[] = [];
  const notices: string[] = [];
  if ("editor" in draft || !Array.isArray(draft.tracks)) throw new Error("模板格式不支持，请重新创建模板");
  const duration = previewDuration(draft, media);
  const canvas = { Width: media?.width ?? 1920, Height: media?.height ?? 1080 };
  const video = {
    Type: "Video", MediaURL: media?.url ?? previewVideoUrl(),
    In: 0, Out: media?.duration ?? 10, TimelineIn: 0, TimelineOut: duration,
    Width: 0.9999, Height: 0.9999, AdaptMode: "Cover",
    Effects: [{ Type: "Volume", Gain: 0 }] as Effect[],
  };
  const timeline = {
    VideoTracks: [{ VideoTrackClips: [video] }],
    SubtitleTracks: [] as { SubtitleTrackClips: NonNullable<ReturnType<typeof buildTrackContent>["text"]>[] }[],
    AudioTracks: [], AspectRatio: `${canvas.Width}:${canvas.Height}`, FECanvas: canvas,
  };
  const seen = new Set<string>();
  let transitionCount = 0;
  for (const source of draft.tracks) {
    const applied = resolveTrack(source, duration);
    if (applied.notice) notices.push(`${trackLabel(source, draft.tracks)}：${applied.notice}`);
    const track = { ...source, ...applied };
    if (!track.id || seen.has(track.id)) throw new Error("特效轨道 ID 必须唯一");
    seen.add(track.id);
    const resolved = buildTrackContent(track.editor, catalog, track.target);
    if (track.end <= track.start) continue;
    if (isTextTarget(track.target)) {
      const text = resolved.text;
      if (!text?.Content.trim()) throw new Error("文字轨道内容不能为空");
      timeline.SubtitleTracks.push({ SubtitleTrackClips: [{ ...text, TimelineIn: track.start, TimelineOut: track.end }] });
    } else if (track.target === "transition") {
      if (++transitionCount > 1) throw new Error("当前母版的两个片段只允许一个转场");
      const effect = resolved.effect;
      if (!effect) throw new Error("转场轨道缺少效果");
      effect.Duration = Math.round((track.end - track.start) * 30) / 30;
      // 源素材首尾相接，播放区间重叠；第二个片段在合成结束时读到源素材末尾。
      timeline.VideoTracks[0].VideoTrackClips = [
        { ...video, Effects: [...video.Effects, effect], Out: track.end, TimelineOut: track.end },
        { ...video, In: track.end, TimelineIn: track.start, Effects: [...video.Effects] },
      ];
    } else {
      const effect = resolved.effect;
      if (!effect) throw new Error("特效轨道缺少效果");
      EffectTracks.push({ EffectTrackItems: [{ ...effect, TimelineIn: track.start, TimelineOut: track.end }] });
    }
    const effectId = { title: track.editor.titleFlower, subtitle: track.editor.subtitleFlower, bubble: track.editor.bubble, filter: track.editor.filter, vfx: track.editor.vfx, transition: track.editor.transition }[track.target];
    const effectName = catalog.find((item) => item.id === effectId)?.name ?? (effectId ? effectId.split("/").at(-1) : undefined);
    previewRows.push({ id: track.id, actions: [{
      id: track.id, targetId: track.id, effectId: track.target,
      start: track.start, end: track.end, movable: true, flexible: true,
      label: trackLabel(track, draft.tracks), effectName, url: "", sourceIn: 0, sourceOut: 0,
    }] });
  }
  return { ...timeline, EffectTracks, previewRows, notices };
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
