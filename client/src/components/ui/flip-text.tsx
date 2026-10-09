/** ObsidianUI 翻转文字：逐字沿 X 轴翻转，按正弦曲线错开延迟形成波浪；loop 控制循环或只播放一次。
 * 与上游不同：上游清单缺少动画 CSS，翻转关键帧由 globals.css 的 --animate-flip-char 提供；
 * 完整文字放在读屏专用节点，逐字动画节点 aria-hidden，避免被逐字朗读。减少动态效果时不播放。
 * Copyright (c) 2026 ObsidianUI — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
import { useMemo, type CSSProperties } from "react";
import { cn } from "@/lib/utils";

/** children 为纯文本；duration 为单次循环秒数，delay 为整体起始延迟。 */
export function FlipText({ className, children, duration = 2.2, delay = 0, loop = true }: {
  className?: string;
  children: string;
  duration?: number;
  delay?: number;
  loop?: boolean;
}) {
  const chars = useMemo(() => Array.from(children), [children]);
  return (
    <span className={cn("inline-block leading-none", className)} style={{ perspective: "1000px" }}>
      <span className="sr-only">{children}</span>
      <span aria-hidden="true" className="inline-block whitespace-nowrap" style={{ transformStyle: "preserve-3d" }}>
        {chars.map((char, index) => (
          <span
            key={index}
            className="inline-block animate-flip-char motion-reduce:animate-none"
            style={{
              animationDuration: `${duration}s`,
              animationDelay: `${Math.sin((index / chars.length) * (Math.PI / 2)) * duration * 0.25 + delay}s`,
              animationIterationCount: loop ? "infinite" : 1,
              transformStyle: "preserve-3d",
            } as CSSProperties}
          >
            {char === " " ? " " : char}
          </span>
        ))}
      </span>
    </span>
  );
}
