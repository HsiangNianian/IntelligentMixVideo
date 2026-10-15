/** 对象时间规则、实例编辑与视频应用计算；计算结果不修改模板配置。 */
import { defaultEditor, type Draft, type EffectDraft, type Editor, type EffectAsset, type EffectTrack, type PreviewMedia } from "./model";
import { appliedTargets, applyAsset, effectTargets, isTextTarget, targetEffectKeys, type EffectTarget } from "./effects";
import { previewVideos } from "./media";
import { compactTransitions, nextTransitionBoundary, setTransitionDuration } from "./transitions";
import { previewFont } from "./fonts";

/** 时间轴自上向下显示的固定图层；同类对象保持添加顺序。 */
export const trackLayers: Record<EffectTarget, number> = { title: 0, subtitle: 1, bubble: 2, transition: 3, vfx: 4, filter: 5 };

/** 应用到视频后的秒数和动画参数，说明用于展示缩短或跳过的对象。 */
export interface ResolvedTrack {
  start: number;
  end: number;
  editor: Editor;
  notice: string;
}

/** 保存规则时检查数值和转场限制，允许位置超过当前预览视频。 */
export function validateTrack(track: EffectTrack): void {
  if (track.target === "subtitle" && (track.start_mode !== "seconds" || track.start !== 0 || track.duration !== null))
    throw new Error("底部字幕固定覆盖整个视频，不能修改时间");
  if (track.target === "transition") {
    if (!Number.isInteger(track.transition_boundary) || track.transition_boundary! < 1 || track.transition_boundary! > 100) throw new Error("转场列表序号必须为 1～100 的整数");
    if (track.start !== undefined || track.start_mode !== undefined) throw new Error("转场只保存交界处与持续时间");
    if (track.duration === null || !Number.isFinite(track.duration) || track.duration < 0.1 || track.duration > 3) throw new Error("转场持续时间为 0.1～3 秒");
    return;
  }
  if (track.transition_boundary !== undefined)
    throw new Error("只有转场可以设置列表序号");
  if (!track.start_mode || !["seconds", "percent"].includes(track.start_mode) || track.start === undefined || !Number.isFinite(track.start) || track.start < 0 || (track.start_mode === "percent" && track.start >= 100))
    throw new Error("开始时间须为非负秒数，或 0% 至小于 100% 的百分比");
  if (track.duration !== null && (!Number.isFinite(track.duration) || track.duration <= 0))
    throw new Error("持续时间须大于零");
}

/** 预览和提交共用数量校验，防止无效对象进入播放器或存储请求。 */
export function validateTemplateTracks(tracks: EffectTrack[]): void {
  if (!Array.isArray(tracks)) throw new Error("模板格式不支持，请重新创建模板");
  const ids = new Set<string>();
  const singletons = new Set<string>();
  const boundaries = new Set<number>();
  for (const track of tracks) {
    validateTrack(track);
    previewFont(track.editor.titleFont);
    previewFont(track.editor.subtitleFont);
    if (isTextTarget(track.target) && !track.editor[track.target === "bubble" ? "bubbleText" : track.target].trim())
      throw new Error("文字轨道内容不能为空");
    if (!track.id || ids.has(track.id)) throw new Error("特效轨道 ID 必须唯一");
    ids.add(track.id);
    if (["title", "subtitle", "vfx"].includes(track.target)) {
      if (singletons.has(track.target)) throw new Error("标题、字幕和 VFX 最多各一个");
      singletons.add(track.target);
    }
    if (track.target === "transition") {
      const boundary = track.transition_boundary!;
      if (boundaries.has(boundary)) throw new Error("每个视频交界处最多一个转场");
      boundaries.add(boundary);
    }
  }
}

/** 按输出帧率计算区间；结尾以外的对象不显示，动画按可用时间等比缩短。 */
export function resolveTrack(track: EffectTrack, duration: number, fps = 30): ResolvedTrack {
  validateTrack(track);
  if (track.target === "transition") throw new Error("转场需要视频片段信息");
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isInteger(fps) || fps <= 0)
    throw new Error("视频时长或帧率无效");
  const start = track.start_mode === "percent" ? duration * track.start! / 100 : track.start!;
  const end = track.duration === null ? duration : start + track.duration;
  const first = Math.round(start * fps);
  const last = Math.min(Math.floor(duration * fps + 1e-8), Math.round(end * fps));
  const editor = { ...track.editor };
  if (first >= last) return { start: first / fps, end: first / fps, editor, notice: "当前视频中没有可显示的时间" };
  let notice = end > duration + 1e-8 ? "持续时间已缩短至视频结束" : "";
  if (isTextTarget(track.target)) {
    const role = track.target;
    const motions = (["In", "Out"] as const).filter((motion) => editor[`${role}${motion}`]);
    const total = motions.reduce((sum, motion) => sum + editor[`${role}${motion}Duration`], 0);
    if (last - first < motions.length) throw new Error("文字显示帧数不足以完成入场与出场动画");
    if (total > (last - first) / fps + 1e-8) {
      let available = last - first;
      motions.forEach((motion, index) => {
        const frames = index === motions.length - 1 ? available : Math.max(1, Math.min(available - 1, Math.round(editor[`${role}${motion}Duration`] / total * (last - first))));
        editor[`${role}${motion}Duration`] = frames / fps;
        available -= frames;
      });
      notice = [notice, "入场与出场动画已按显示时间缩短"].filter(Boolean).join("；");
    }
  }
  return { start: first / fps, end: last / fps, editor, notice };
}

/** 片段来源已经确定；居中预览保持视频长度，顺序拼接计算重叠，固定时间保留素材位置。 */
export interface TimelineContext {
  layout: "sequence" | "fixed" | "centered";
  clips: readonly { duration: number; start?: number }[];
  fps: number;
  duration?: number;
}

/** 本次应用的完整时间结果，展示与渲染共用，不写回模板。 */
export interface ResolvedTimeline {
  duration: number;
  clips: { start: number; end: number }[];
  tracks: Record<string, ResolvedTrack>;
  transitions: { start: number; end: number }[];
  notices: string[];
}

/** 将已读取的三个来源按当前排列转换为居中转场的预览输入。 */
export function previewContext(media: PreviewMedia, order = [0, 1, 2]): TimelineContext {
  if (order.length !== 3 || new Set(order).size !== 3 || order.some((value) => ![0, 1, 2].includes(value))) throw new Error("视频排列无效");
  const sources = previewVideos(media);
  return { layout: "centered", fps: 30, clips: order.map((source) => ({ duration: sources[source].duration })) };
}

/** 校验规则后统一计算帧区间；缺少交界处或素材过短均产生明确的应用说明。 */
export function resolveTemplate(draft: Pick<Draft, "tracks"> & Partial<Pick<Draft, "clips" | "transition_mode">>, context: TimelineContext): ResolvedTimeline {
  validateTemplateTracks(draft.tracks);
  const { fps, layout } = context;
  if (!["sequence", "fixed", "centered"].includes(layout) || !Number.isInteger(fps) || fps <= 0 || fps > 60 || !context.clips.length)
    throw new Error("视频时间信息无效");
  const lengths = context.clips.map((clip) => {
    if (!Number.isFinite(clip.duration) || clip.duration <= 0) throw new Error("视频片段时长无效");
    const frames = Math.floor(clip.duration * fps + 1e-8);
    if (!frames && layout !== "fixed") throw new Error("视频片段不足一帧");
    return frames;
  });
  const overlaps = lengths.map(() => 0);
  const transitionNotices = new Map<string, string>();
  for (const track of [...draft.tracks.filter((item) => item.target === "transition"), ...(draft.clips ?? []).filter((clip) => clip.transition_boundary !== undefined)]) {
    const index = track.transition_boundary! - 1;
    if (index + 1 >= lengths.length) {
      if (!draft.transition_mode || index < 2) transitionNotices.set(track.id, `交界处 ${index + 1} 不存在，未应用转场`);
      continue;
    }
    const requested = Math.round(track.duration! * fps);
    const frames = Math.min(requested, Math.floor(lengths[index] / 2), Math.floor(lengths[index + 1] / 2));
    if (frames < Math.ceil(0.1 * fps)) transitionNotices.set(track.id, "相邻片段过短，未应用转场");
    else {
      overlaps[index] = frames;
      if (frames < requested) transitionNotices.set(track.id, "转场已按相邻片段可用时间缩短");
    }
  }
  let end = 0;
  const clips = lengths.map((frames, index) => {
    const fixedStart = context.clips[index].start;
    if (layout === "fixed" && (fixedStart === undefined || !Number.isFinite(fixedStart) || fixedStart < 0)) throw new Error("固定片段缺少开始时间");
    if (layout === "fixed") {
      if (fixedStart! + 1e-8 < end) throw new Error("固定片段时间重叠");
      end = fixedStart! + context.clips[index].duration;
      return { start: fixedStart!, end };
    }
    const start = end - (layout === "sequence" ? overlaps[index - 1] ?? 0 : 0);
    end = start + frames;
    return { start: start / fps, end: end / fps };
  });
  const duration = layout === "fixed" ? context.duration : end / fps;
  if (duration === undefined || !Number.isFinite(duration) || duration <= 0 || clips.at(-1)!.end > duration + 1e-8) throw new Error("视频总时长无效");
  const tracks: ResolvedTimeline["tracks"] = {};
  const transitions = clips.slice(0, -1).map((clip, index) => {
    const frames = overlaps[index];
    const last = layout === "centered" ? (Math.round(clip.end * fps) + Math.ceil(frames / 2)) / fps : clip.end;
    return { start: last - frames / fps, end: last };
  });
  const notices: string[] = [];
  for (const track of draft.tracks) {
    const index = track.transition_boundary! - 1;
    const boundaryEnd = clips[index]?.end ?? 0;
    const resolved = track.target === "transition"
      ? { ...(transitions[index] ?? { start: boundaryEnd, end: boundaryEnd }), editor: track.editor, notice: transitionNotices.get(track.id) ?? "" }
      : resolveTrack(track, duration, fps);
    tracks[track.id] = resolved;
    if (resolved.notice) notices.push(`${trackLabel(track, draft.tracks)}：${resolved.notice}`);
  }
  return { duration, clips, tracks, transitions, notices };
}

/** 提取当前对象参数，供参数面板和 SDK 转换共用。 */
export function trackEditor(editor: Editor, target: EffectTarget): Editor {
  const result = { ...defaultEditor, title: "", subtitle: "", bubbleText: "" };
  for (const field of targetEffectKeys(target)) result[field] = editor[field];
  if (isTextTarget(target)) {
    const key = target === "bubble" ? "bubbleText" : target;
    result[key] = editor[key];
    if (target !== "bubble") result[`${target}Font`] = editor[`${target}Font`];
    for (const suffix of ["Size", "X", "Y", "InDuration", "OutDuration"] as const)
      result[`${target}${suffix}`] = editor[`${target}${suffix}`];
    if (target === "title") {
      result.titleKeyword = editor.titleKeyword;
      result.titleKeywordBold = editor.titleKeywordBold;
      result.titleKeywordItalic = editor.titleKeywordItalic;
      result.titleKeywordUnderline = editor.titleKeywordUnderline;
      result.titleKeywordStrikeout = editor.titleKeywordStrikeout;
      result.titleKeywordColor = editor.titleKeywordColor;
      result.titleKeywordSize = editor.titleKeywordSize;
    }
    if (target === "subtitle") {
      result.subtitleKeywordBold = editor.subtitleKeywordBold;
      result.subtitleKeywordItalic = editor.subtitleKeywordItalic;
      result.subtitleKeywordUnderline = editor.subtitleKeywordUnderline;
      result.subtitleKeywordStrikeout = editor.subtitleKeywordStrikeout;
      result.subtitleKeywordColor = editor.subtitleKeywordColor;
      result.subtitleKeywordSize = editor.subtitleKeywordSize;
    }
  }
  return result;
}

/** 为选中实例建立参数面板视图。 */
export function trackDraft(draft: Draft, track: EffectTrack): EffectDraft {
  return { editor: track.editor,
    transition_duration_seconds: track.target === "transition" ? track.duration! : draft.transition_duration_seconds };
}

/** 参数修改只替换指定实例，清除画面效果时移除对应对象。 */
export function updateTrack(draft: Draft, id: string, edit: EffectDraft): Draft {
  const tracks = draft.tracks;
  const current = tracks.find((track) => track.id === id);
  if (!current) throw new Error("特效轨道不存在");
  if (!isTextTarget(current.target) && !appliedTargets(edit).includes(current.target)) return removeTrack(draft, id);
  const updated = { ...current, editor: trackEditor(edit.editor, current.target),
    duration: current.target === "transition" ? edit.transition_duration_seconds : current.duration };
  validateTrack(updated);
  const next = { ...draft, tracks: tracks.map((track) => track.id === id ? updated : track) };
  return current.target === "transition" ? setTransitionDuration(next, edit.transition_duration_seconds) : next;
}

/** 删除指定实例，其他对象的时间规则保持不变。 */
export function removeTrack(draft: Draft, id: string): Draft {
  if (draft.tracks.some((track) => track.id === id && (track.target === "title" || track.target === "subtitle")))
    throw new Error("标题和底部字幕为固定对象");
  const next = { ...draft, tracks: draft.tracks.filter((track) => track.id !== id) };
  return draft.tracks.some((track) => track.id === id && track.target === "transition") ? compactTransitions(next) : next;
}

/** 添加效果；转场追加到列表，指定序号时修改对应效果；滤镜可重复添加。 */
export function addTrack(draft: Draft, asset: EffectAsset, target: EffectTarget, selectedId?: string, boundary?: number): { draft: Draft; id: string } {
  const selected = draft.tracks.find((track) => track.id === selectedId);
  if (["in", "out", "loop"].includes(asset.category)) {
    if (!selected || !isTextTarget(selected.target)) throw new Error("请选择需要添加动画的文字轨道");
    const applied = applyAsset(trackDraft(draft, selected), asset, selected.target);
    return { draft: updateTrack(draft, selected.id, applied.draft), id: selected.id };
  }
  const role = isTextTarget(target) ? target : "title";
  if (asset.category === "transition/normal" && boundary === undefined) {
    boundary = nextTransitionBoundary(draft);
  }
  if (asset.category === "transition/normal" && draft.clips.some((clip) => clip.transition_boundary === boundary))
    throw new Error(`交界处 ${boundary} 已有 Remotion 转场，请先移除`);
  const transition = asset.category === "transition/normal" ? draft.tracks.find((track) => track.target === "transition" && track.transition_boundary === boundary) : undefined;
  if (transition) {
    const applied = applyAsset(trackDraft(draft, transition), asset, role);
    return { draft: updateTrack(draft, transition.id, applied.draft), id: transition.id };
  }
  const styleTarget = asset.category === "bubble" ? "bubble" : asset.category === "flower" ? role : null;
  const styleField = styleTarget === "bubble" ? "bubble" : styleTarget ? `${styleTarget}Flower` as const : null;
  const unstyled = asset.category === "vfx/normal" ? draft.tracks.find((track) => track.target === "vfx") : styleTarget && styleField ? draft.tracks.find((track) => track.target === styleTarget && (styleTarget !== "bubble" || !track.editor[styleField])) : undefined;
  if (unstyled) {
    const applied = applyAsset(trackDraft(draft, unstyled), asset, role);
    return { draft: updateTrack(draft, unstyled.id, applied.draft), id: unstyled.id };
  }
  const applied = applyAsset({ transition_duration_seconds: draft.transition_duration_seconds, editor: { ...defaultEditor } }, asset, role);
  const track: EffectTrack = {
    id: crypto.randomUUID(), target: applied.target,
    ...(applied.target === "transition" ? {} : { start_mode: "seconds" as const, start: 0 }),
    duration: applied.target === "transition" ? draft.transition_duration_seconds : null,
    ...(applied.target === "transition" ? { transition_boundary: boundary } : {}),
    editor: trackEditor(applied.draft.editor, applied.target),
  };
  validateTrack(track);
  if (draft.tracks.length + draft.clips.length >= 100) throw new Error("轨道数量不能超过 100");
  return { draft: { ...draft, tracks: [...draft.tracks, track] }, id: track.id };
}

/** 参数表单保存规则，不依赖预览视频长度。 */
export function setTrackTiming(draft: Draft, id: string, timing: Pick<EffectTrack, "start_mode" | "start" | "duration">): Draft {
  if (!draft.tracks.some((track) => track.id === id)) throw new Error("特效轨道不存在");
  return { ...draft, tracks: draft.tracks.map((track) => {
    if (track.id !== id) return track;
    const next = { ...track, ...timing };
    validateTrack(next);
    return next;
  }) };
}

/** 同类对象显示实例序号。 */
export function trackLabel(track: EffectTrack, tracks: EffectTrack[]): string {
  if (track.target === "transition") return `镜头转场 ${track.transition_boundary}`;
  const siblings = tracks.filter((item) => item.target === track.target);
  return siblings.length === 1 ? effectTargets[track.target] : `${effectTargets[track.target]} ${siblings.findIndex((item) => item.id === track.id) + 1}`;
}
