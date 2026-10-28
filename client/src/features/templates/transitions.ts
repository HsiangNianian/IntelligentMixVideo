/** 转场候选列表、预览选择与统一时长的编辑操作。 */
import type { Draft } from "./model";
import type { SpriteAsset } from "@/features/sprites/model";

/** IMS 和 Remotion 转场共同使用一个有序列表。 */
export function transitionItems(draft: Pick<Draft, "tracks" | "clips">) {
  return [...draft.tracks.filter((track) => track.target === "transition"), ...draft.clips.filter((clip) => clip.transition_boundary !== undefined)]
    .sort((a, b) => a.transition_boundary! - b.transition_boundary!);
}

/** 预览生成两个转场实例，单个候选由两处共用；实例映射回候选，随机数由工作区持有。 */
export function previewTransitions(draft: Draft, random: readonly [number, number], assets: readonly Pick<SpriteAsset, "id" | "seconds">[] = []) {
  const items = transitionItems(draft);
  const targets = new Map<string, string>();
  const tracks = draft.tracks.filter((track) => track.target !== "transition");
  const clips = draft.clips.filter((clip) => clip.transition_boundary === undefined);
  if (draft.transition_enabled !== false && items.length) {
    for (let index = 0; index < 2; index += 1) {
      const position = draft.transition_mode === "random" ? Math.floor(random[index] * items.length) : index % items.length;
      const item = items[position];
      const id = targets.has(item.id) ? `${item.id}:preview:${index + 1}` : item.id;
      targets.set(id, item.id);
      if ("editor" in item) tracks.push({ ...item, id, transition_boundary: index + 1, duration: draft.transition_duration_seconds });
      else clips.push({ ...item, id, transition_boundary: index + 1,
        duration: item.duration ?? assets.find((asset) => asset.id === item.sprite_id)?.seconds ?? null });
    }
  }
  return { draft: { ...draft, tracks, clips }, targets };
}

/** 优先填充尚未设置的预览位置，随后扩展转场列表。 */
export function nextTransitionBoundary(draft: Pick<Draft, "tracks" | "clips">): number {
  const occupied = new Set(transitionItems(draft).map((item) => item.transition_boundary));
  let boundary = 1;
  while (occupied.has(boundary)) boundary += 1;
  return boundary;
}

/** 删除后按列表顺序连续编号，让后续效果进入前两个预览位置。 */
export function compactTransitions(draft: Draft): Draft {
  const positions = new Map(transitionItems(draft).map((item, index) => [item.id, index + 1]));
  return { ...draft,
    tracks: draft.tracks.map((track) => positions.has(track.id) ? { ...track, transition_boundary: positions.get(track.id)! } : track),
    clips: draft.clips.map((clip) => positions.has(clip.id) ? { ...clip, transition_boundary: positions.get(clip.id)! } : clip),
  };
}

/** 统一时长在输入处校验，随后更新全部 IMS 转场，包含当前没有显示的条目。 */
export function setTransitionDuration(draft: Draft, seconds: number): Draft {
  if (!Number.isFinite(seconds) || seconds < 0.1 || seconds > 3) throw new Error("转场持续时间为 0.1～3 秒");
  return { ...draft, transition_duration_seconds: seconds,
    tracks: draft.tracks.map((track) => track.target === "transition" ? { ...track, duration: seconds } : track),
  };
}
