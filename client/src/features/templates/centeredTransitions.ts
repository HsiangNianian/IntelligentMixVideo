/** 提取相邻视频的首尾画面，生成保持总时长的居中转场预览。 */
import { loadVideoFrames } from "./thumbnails";
import { playerTimeline, transitionFrameKey, type buildTimeline } from "./timeline";

/** SDK 的图片重叠转场放在视频上方，按交界处前后各半段显示，保持视频及文字的原有时间。 */
export async function centeredPlayerTimeline(timeline: ReturnType<typeof buildTimeline>, signal: AbortSignal, cache: Map<string, string>, maskImages?: ReadonlyMap<string, HTMLImageElement>) {
  const frames = new Map<string, string>();
  const videos = timeline.previewVideoTracks[0].VideoTrackClips;
  for (const transition of timeline.transitions) {
    for (const index of [0, 1]) {
      const video = videos[transition.boundary - 1 + index];
      const time = index === 0 ? Math.max(video.In, video.Out - 1 / 30) : video.In;
      const [image] = await loadVideoFrames(video.MediaURL, [time], signal, cache);
      frames.set(transitionFrameKey(transition.boundary, index), image);
    }
  }
  signal.throwIfAborted();
  const playback = playerTimeline(timeline, frames, maskImages);
  const images = new Map<string, Promise<string>>();
  for (const row of playback.VideoTracks) {
    for (const clip of row.VideoTrackClips) {
      if (!("MediaURL" in clip) || typeof clip.MediaURL !== "string" || !clip.MediaURL.startsWith("data:image/svg+xml")) continue;
      const svg = clip.MediaURL;
      if (!images.has(svg)) images.set(svg, (async () => {
        const image = new Image(); image.src = svg; await image.decode();
        signal.throwIfAborted();
        const canvas = document.createElement("canvas");
        canvas.width = image.width; canvas.height = image.height;
        canvas.getContext("2d")!.drawImage(image, 0, 0);
        return canvas.toDataURL("image/png");
      })());
      clip.MediaURL = await images.get(svg)!;
    }
  }
  return playback;
}
