/** PR76 固定主画布；实例位置、时间和内容由聊天需求与组合结果确定。 */
import type { CompositionDraft } from "./composition";

/** 保留调用方的会话配置接口，界面只说明当前固定规格。 */
export function CompositionSettings(_props: {
  value: CompositionDraft;
  disabled: boolean;
  onChange: (value: CompositionDraft) => void;
  spriteKind?: "text" | "subtitle" | "filter_overlay" | "video_overlay" | "transition_overlay";
  onSpriteKindChange?: (kind: "text" | "subtitle" | "filter_overlay" | "video_overlay" | "transition_overlay") => void;
}) {
  return <section aria-label="生成配置" className="space-y-2 rounded-xl border bg-muted/30 p-3">
    <p className="text-sm font-medium">竖屏 1080×1920 · 30 FPS</p>
    <p className="text-xs text-muted-foreground">描述你需要的内容、布局和动画。最终时长由各实例的时间编排确定。</p>
  </section>;
}
