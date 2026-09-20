/** 对象时间规则、实例编辑与视频应用计算；计算结果不修改模板配置。 */
import { defaultEditor, type Draft, type Editor, type EffectAsset, type EffectTrack, type MasterVideo } from "./model";
import { appliedTargets, applyAsset, effectTargets, isTextTarget, targetEffectKeys, type EffectTarget } from "./effects";

/** 应用到视频后的秒数和动画参数，说明用于展示缩短或跳过的对象。 */
export interface ResolvedTrack {
  start: number;
  end: number;
  editor: Editor;
  notice: string;
}

/** 保存规则时检查数值和转场限制，允许位置超过当前预览视频。 */
export function validateTrack(track: EffectTrack): void {
  if (!["seconds", "percent"].includes(track.start_mode) || !Number.isFinite(track.start) || track.start < 0 || (track.start_mode === "percent" && track.start >= 100))
    throw new Error("开始时间须为非负秒数，或 0% 至小于 100% 的百分比");
  if (track.duration !== null && (!Number.isFinite(track.duration) || track.duration <= 0))
    throw new Error("持续时间须大于零");
  if (track.target === "transition" && (track.start <= 0 || track.duration === null || track.duration < 0.1 || track.duration > 3))
    throw new Error("转场开始位置须大于零，持续时间为 0.1～3 秒");
}

/** 按输出帧率计算区间；结尾以外的对象不显示，动画按可用时间等比缩短。 */
export function resolveTrack(track: EffectTrack, duration: number, fps = 30): ResolvedTrack {
  validateTrack(track);
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isInteger(fps) || fps <= 0)
    throw new Error("视频时长或帧率无效");
  const start = track.start_mode === "percent" ? duration * track.start / 100 : track.start;
  const end = track.duration === null ? duration : start + track.duration;
  const first = Math.round(start * fps);
  const last = Math.min(Math.floor(duration * fps + 1e-8), track.target === "transition" ? first + Math.round(track.duration! * fps) : Math.round(end * fps));
  const editor = { ...track.editor };
  if (track.target === "transition" && (first <= 0 || last >= Math.floor(duration * fps + 1e-8) || end > duration))
    throw new Error("转场须位于两个非空视频片段之间");
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

/** 预览转场连接连续源片段，合成时长扣除按帧计算的重叠时间。 */
export function previewDuration(draft: Draft, media?: MasterVideo): number {
  const transition = draft.tracks?.find((track) => track.target === "transition");
  if (transition) validateTrack(transition);
  return (media?.duration ?? 10) - (transition ? Math.round(transition.duration! * 30) / 30 : 0);
}

/** 提取当前对象参数，供参数面板和 SDK 转换共用。 */
export function trackEditor(editor: Editor, target: EffectTarget): Editor {
  const result = { ...defaultEditor, title: "", subtitle: "", bubbleText: "" };
  for (const field of targetEffectKeys(target)) result[field] = editor[field];
  if (isTextTarget(target)) {
    const key = target === "bubble" ? "bubbleText" : target;
    result[key] = editor[key];
    for (const suffix of ["Size", "X", "Y", "InDuration", "OutDuration"] as const)
      result[`${target}${suffix}`] = editor[`${target}${suffix}`];
  }
  return result;
}

/** 为尚未建立对象数组的编辑草稿生成独立实例。 */
export function withTracks(draft: Draft): Draft & { tracks: EffectTrack[] } {
  return { ...draft, tracks: draft.tracks ?? appliedTargets(draft).map((target) => ({
    id: target, target, start_mode: target === "transition" ? "percent" : "seconds",
    start: target === "transition" ? 50 : 0,
    duration: target === "transition" ? draft.transition_duration_seconds : null,
    editor: trackEditor(draft.editor, target),
  })) };
}

/** 为选中实例建立参数面板视图。 */
export function trackDraft(draft: Draft, track: EffectTrack): Draft {
  return { ...draft, tracks: undefined, editor: track.editor,
    transition_duration_seconds: track.target === "transition" ? track.duration! : draft.transition_duration_seconds };
}

/** 参数修改只替换指定实例，清除画面效果时移除对应对象。 */
export function updateTrack(draft: Draft, id: string, edit: Draft): Draft {
  const tracks = withTracks(draft).tracks;
  const current = tracks.find((track) => track.id === id);
  if (!current) throw new Error("特效轨道不存在");
  if (!isTextTarget(current.target) && !appliedTargets(edit).includes(current.target)) return removeTrack(draft, id);
  const updated = { ...current, editor: trackEditor(edit.editor, current.target),
    duration: current.target === "transition" ? edit.transition_duration_seconds : current.duration };
  validateTrack(updated);
  return { ...draft, tracks: tracks.map((track) => track.id === id ? updated : track) };
}

/** 删除指定实例，其他对象的时间规则保持不变。 */
export function removeTrack(draft: Draft, id: string): Draft {
  return { ...draft, tracks: withTracks(draft).tracks.filter((track) => track.id !== id) };
}

/** 添加独立实例；动画属于选中文字，转场保留单个切换位置。 */
export function addTrack(draft: Draft, asset: EffectAsset, target: EffectTarget, selectedId?: string): { draft: Draft; id: string } {
  const normalized = withTracks(draft);
  const selected = normalized.tracks.find((track) => track.id === selectedId);
  if (["in", "out", "loop"].includes(asset.category)) {
    if (!selected || !isTextTarget(selected.target)) throw new Error("请选择需要添加动画的文字轨道");
    const applied = applyAsset(trackDraft(draft, selected), asset, selected.target);
    return { draft: updateTrack(normalized, selected.id, applied.draft), id: selected.id };
  }
  const role = isTextTarget(target) ? target : "title";
  const transition = asset.category === "transition/normal" ? normalized.tracks.find((track) => track.target === "transition") : undefined;
  if (transition) {
    const applied = applyAsset(trackDraft(draft, transition), asset, role);
    return { draft: updateTrack(normalized, transition.id, applied.draft), id: transition.id };
  }
  const styleTarget = asset.category === "bubble" ? "bubble" : asset.category === "flower" ? role : null;
  const styleField = styleTarget === "bubble" ? "bubble" : styleTarget ? `${styleTarget}Flower` as const : null;
  const unstyled = styleTarget && styleField ? normalized.tracks.find((track) => track.target === styleTarget && !track.editor[styleField]) : undefined;
  if (unstyled) {
    const applied = applyAsset(trackDraft(draft, unstyled), asset, role);
    return { draft: updateTrack(normalized, unstyled.id, applied.draft), id: unstyled.id };
  }
  const applied = applyAsset({ ...draft, tracks: undefined, editor: { ...defaultEditor } }, asset, role);
  const track: EffectTrack = {
    id: crypto.randomUUID(), target: applied.target,
    start_mode: applied.target === "transition" ? "percent" : "seconds",
    start: applied.target === "transition" ? 50 : 0,
    duration: applied.target === "transition" ? draft.transition_duration_seconds : null,
    editor: trackEditor(applied.draft.editor, applied.target),
  };
  validateTrack(track);
  if (normalized.tracks.length >= 100) throw new Error("轨道数量不能超过 100");
  return { draft: { ...normalized, tracks: [...normalized.tracks, track] }, id: track.id };
}

/** 参数表单保存规则，不依赖预览视频长度。 */
export function setTrackTiming(draft: Draft, id: string, timing: Pick<EffectTrack, "start_mode" | "start" | "duration">): Draft {
  const normalized = withTracks(draft);
  if (!normalized.tracks.some((track) => track.id === id)) throw new Error("特效轨道不存在");
  return { ...normalized, tracks: normalized.tracks.map((track) => {
    if (track.id !== id) return track;
    const next = { ...track, ...timing };
    validateTrack(next);
    return next;
  }) };
}

/** 时间轴拖动保留开始方式；移动保留持续规则，调整右侧位置改为固定时长。 */
export function setTrackRange(draft: Draft, id: string, start: number, end: number, media?: MasterVideo): Draft {
  const track = withTracks(draft).tracks.find((item) => item.id === id);
  if (!track) throw new Error("特效轨道不存在");
  const duration = previewDuration(draft, media);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > duration)
    throw new Error("时间轴区间须位于当前视频范围内");
  const previous = resolveTrack(track, duration);
  const moved = Math.abs(end - start - (previous.end - previous.start)) < 0.0001;
  const length = moved || (track.duration === null && Math.abs(end - previous.end) < 0.0001) ? track.duration : Math.round((end - start) * 10000) / 10000;
  const nextDuration = track.target === "transition" ? (media?.duration ?? 10) - Math.round(length! * 30) / 30 : duration;
  return setTrackTiming(draft, id, {
    start_mode: track.start_mode,
    start: track.start_mode === "percent" ? start / nextDuration * 100 : start,
    duration: length,
  });
}

/** 同类对象显示实例序号。 */
export function trackLabel(track: EffectTrack, tracks: EffectTrack[]): string {
  const siblings = tracks.filter((item) => item.target === track.target);
  return siblings.length === 1 ? effectTargets[track.target] : `${effectTargets[track.target]} ${siblings.findIndex((item) => item.id === track.id) + 1}`;
}
