/** ObsidianUI 融合小球加载器：三个小球左右摆动，经 SVG 滤镜融合成液态效果；减少动态效果时静止。
 * 与上游不同：默认作为装饰（aria-hidden），加载文字由调用方的 role="status" 元素提供，避免重复播报。
 * Copyright (c) 2026 ObsidianUI — MIT；完整声明见 client/public/THIRD_PARTY_NOTICES.txt。
 */
"use client";

import { useId } from "react";
import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import { cn } from "@/lib/utils";

/** size 为单个小球直径（像素），color 默认跟随文字颜色，duration 为一次往返的秒数。 */
export interface LoaderGooeyBlobsProps extends Omit<HTMLMotionProps<"div">, "children"> {
  size?: number;
  color?: string;
  duration?: number;
}

/** 每个实例使用独立滤镜 ID，同页多个加载器互不干扰。 */
export function LoaderGooeyBlobs({
  className,
  size = 20,
  color = "currentColor",
  duration = 1.5,
  style,
  ...props
}: LoaderGooeyBlobsProps) {
  const filterId = `gooey-${useId().replace(/:/g, "")}`;
  const reducedMotion = useReducedMotion();
  // 摆动幅度随小球尺寸缩放，小尺寸用于行内时不会越出容器。
  const swing = size * 0.75;

  return (
    <motion.div
      aria-hidden="true"
      className={cn("flex items-center justify-center", className)}
      style={style}
      {...props}
    >
      <svg aria-hidden="true" focusable="false" className="absolute h-0 w-0">
        <defs>
          <filter id={filterId}>
            <feGaussianBlur in="SourceGraphic" stdDeviation={size / 6} result="blur" />
            <feColorMatrix in="blur" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -7" result="gooey" />
            <feBlend in="SourceGraphic" in2="gooey" />
          </filter>
        </defs>
      </svg>
      <span aria-hidden="true" className="flex gap-1" style={{ filter: `url(#${filterId})` }}>
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className="rounded-full"
            style={{ width: size, height: size, backgroundColor: color }}
            animate={reducedMotion ? undefined : { x: [0, swing, 0, -swing, 0], scale: [1, 1.2, 1, 1.2, 1] }}
            transition={{ duration, ease: "easeInOut", repeat: Infinity, delay: index * 0.2 }}
          />
        ))}
      </span>
    </motion.div>
  );
}
