"""Executable examples sent to the Executor; browser regressions render the same source.

These explain the role contract, not installed Presets. A generated Preset still owns its appearance.
"""

TYPEWRITER_CODE = '''import React from "react";
export default function Typewriter(props: {
  children: React.ReactNode;
  progress: number;
  completedCount: number;
  index: number;
  count: number;
  Box: React.ComponentType<{style?: React.CSSProperties; children?: React.ReactNode}>;
  cursorColor: string;
  cursorWidth: number;
}) {
  const {Box, completedCount, index, count} = props;
  const cursor = completedCount < count && index === Math.max(0, completedCount - 1);
  return <Box style={{position: "relative"}}>
    <span style={{visibility: index < completedCount ? "visible" : "hidden"}}>{props.children}</span>
    {cursor && props.cursorWidth > 0 ? <span aria-hidden style={{
      position: "absolute", top: "0.1em", height: "0.95em", width: props.cursorWidth,
      left: completedCount === 0 ? 0 : undefined, right: completedCount > 0 ? 0 : undefined,
      backgroundColor: props.cursorColor, pointerEvents: "none",
    }} /> : null}
  </Box>;
}
'''

# Complementary copies keep the original layout while their visible fragments move independently.
SPLIT_CODE = '''import React from "react";
export default function Split(props: {
  children: React.ReactNode;
  progress: number;
  Box: React.ComponentType<{style?: React.CSSProperties; children?: React.ReactNode}>;
  distance: number;
}) {
  const {Box} = props;
  const p = Math.max(0, Math.min(1, props.progress));
  const move = p * p * (3 - 2 * p) * props.distance;
  const opacity = 1 - Math.max(0, (p - 0.55) / 0.45);
  return <Box style={{position: "relative", opacity}}>
    <div style={{clipPath: "inset(0 0 50% 0)", transform: `translateY(${-move}em)`}}>{props.children}</div>
    <div style={{position: "absolute", inset: 0, clipPath: "inset(50% 0 0 0)", transform: `translateY(${move}em)`}}>{props.children}</div>
  </Box>;
}
'''
