/**
 * 字效渲染与视频合成共用的时间宿主：只打包已封存的资产，不执行它，也不修改发布或预览文件。
 * 复用锁定的 Remotion 上下文，使固定 Sequence、硬编码帧动画和 useVideoConfig 使用同一原始时钟。
 */
import fs from "node:fs";
import {build} from "../remotion/node_modules/esbuild/lib/main.js";

const {code, frames, fps, still} = JSON.parse(fs.readFileSync(0, "utf8"));
const entry = `
import React from 'react';
import {Internals, useCurrentFrame, useVideoConfig} from 'remotion';
import Source from 'imv:source';
/** 外层 Sequence 保持 ASR 区间，只有资产子树按发布时钟从首帧播放到尾帧。 */
export default function TimedSprite(props) {
  const frame = useCurrentFrame();
  const config = useVideoConfig();
  const contexts = Internals.useRemotionContexts();
  // Remotion 只提供整数帧；资产可能按帧下标取字符或判断奇偶，映射结果取最近的发布帧。
  const sourceFrame = config.durationInFrames <= 2 ? ${still}
    : Math.max(0, Math.min(${frames - 1}, Math.round(frame * ${frames - 1} / (config.durationInFrames - 1))));
  // Rounded source frames can repeat when slowed down. Until a cue really starts, keep its mapped start ahead
  // of the sampled frame so a later line cannot appear early. Callers always supply actual cue-frame offsets.
  const sourceProps = Array.isArray(props.lineStartFrames) ? {...props,
    lineStartFrames: props.lineStartFrames.map((start) => {
      if (config.durationInFrames <= 2) return 0;
      const mapped = Math.max(0, Math.min(${frames - 1}, Math.round(start * ${frames - 1} / (config.durationInFrames - 1))));
      return frame < start ? Math.max(mapped, sourceFrame + 1) : mapped;
    }),
  } : props;
  const sourceConfig = {...config, durationInFrames: ${frames}, fps: ${fps}};
  const clock = {
    ...contexts,
    compositionManagerCtx: {...contexts.compositionManagerCtx, currentCompositionMetadata: sourceConfig},
    timelineContext: {...contexts.timelineContext, frame: {[config.id]: sourceFrame}},
    sequenceContext: {...contexts.sequenceContext, absoluteFrom: 0, cumulatedFrom: 0, relativeFrom: 0,
      parentFrom: 0, cumulatedNegativeFrom: 0, durationInFrames: ${frames}},
  };
  return <Internals.RemotionContextProvider contexts={clock}><Source {...sourceProps}/></Internals.RemotionContextProvider>;
}`;

const result = await build({
  stdin: {contents: entry, sourcefile: "TimedSprite.tsx", loader: "tsx"},
  bundle: true, write: false, format: "esm", platform: "browser", jsx: "automatic",
  plugins: [{
    name: "sealed-sprite",
    setup(plugin) {
      // 与上游组合器相同的导入边界；不允许资产打包时读取磁盘或额外依赖。
      plugin.onResolve({filter: /.*/}, ({path}) => {
        if (path === "imv:source") return {path, namespace: "sealed-sprite"};
        if (["react", "react/jsx-runtime", "react/jsx-dev-runtime", "remotion"].includes(path)) return {path, external: true};
        throw new Error("Unsupported Sprite import");
      });
      plugin.onLoad({filter: /.*/, namespace: "sealed-sprite"}, () => ({contents: code, loader: "tsx"}));
    },
  }],
});
process.stdout.write(JSON.stringify({code: result.outputFiles[0].text}));
