/** Resolve authoring-only Sprite sample copy and preview windows without changing saved style bindings. */
import { SpriteTarget, type SpritePlacement } from "@/generated/imv/sprite/v1/sprite_pb";

export interface SpritePreviewCopy {
  text: string;
  keywords: string;
}

/** The editor supplies examples; the composition bus still owns actual copy and time. */
export function sampleCopy(target: SpriteTarget): SpritePreviewCopy {
  return target === SpriteTarget.SUBTITLE
    ? { text: "这是一句示例字幕，关键词会高亮", keywords: "关键词" }
    : { text: "示例标题", keywords: "" };
}

export interface SpritePreviewWindow {
  start: number;
  end: number;
}

/** Place boundary effects at an example edge and a subtitle in a two-second sample sentence. */
export function previewWindow(placement: SpritePlacement, duration: number): SpritePreviewWindow | null {
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const length = placement.duration ?? duration;
  let start: number;
  let end: number;
  switch (placement.target) {
    case SpriteTarget.VIDEO_ENTER:
      start = 0;
      end = Math.min(duration, length);
      break;
    case SpriteTarget.VIDEO_EXIT:
      end = duration;
      start = Math.max(0, end - length);
      break;
    case SpriteTarget.TRANSITION:
      start = Math.max(0, duration / 2 - length / 2);
      end = Math.min(duration, start + length);
      break;
    default:
      start = placement.startMode === "percent" ? duration * placement.start / 100 : placement.start;
      end = Math.min(duration, start + length);
      if (placement.duration === undefined) end = duration;
      if (placement.target === SpriteTarget.SUBTITLE)
        end = Math.min(end, start + 2);
  }
  return Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start
    ? { start, end }
    : null;
}
