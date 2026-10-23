/**
 * IMS 渲染不可用时的基础视频预览：按解析后的片段顺序播放三个视频，由预览的播放按钮、进度条和时间轴控制；
 * 不渲染 IMS 文字、转场、滤镜和 VFX，播放时逐帧把全局时间回报给 AI 资产叠加层与时间轴。
 */
import { useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { previewVideos } from "./media";
import type { PreviewMedia } from "./model";
import type { ResolvedTimeline } from "./tracks";
import type { PlaybackHandle } from "./previewClock";

/** 返回 time 所在片段的序号：片段首尾相接，交界处属于后一段，超出结尾属于最后一段。 */
export function clipAt(clips: ResolvedTimeline["clips"], time: number) {
  let index = 0;
  while (index + 1 < clips.length && time >= clips[index + 1].start) index++;
  return index;
}

/**
 * 基础播放器只依赖已读取的媒体与解析后的片段区间，不创建 SDK 或请求授权。
 * 每段一个预加载的静音视频（与 IMS 预览一样不出声），换段时切换显示而不重新加载；每段只播到解析出的段长。
 */
export function BasicVideoPreview({ ref, media, resolved, order, onTime, onReady, onError, onPlayingChange, onEnd }: {
  ref: Ref<PlaybackHandle>;
  media: PreviewMedia;
  resolved: ResolvedTimeline;
  order: number[];
  /** 播放时每帧回报一次，定位与换段时立即回报。 */
  onTime: (time: number) => void;
  onReady: () => void;
  onError: () => void;
  onPlayingChange: (playing: boolean) => void;
  onEnd: () => void;
}) {
  const sources = useMemo(() => previewVideos(media), [media]);
  const videos = useRef<(HTMLVideoElement | null)[]>([]);
  const callbacks = useRef({ onTime, onReady, onError, onPlayingChange, onEnd });
  const timeline = useRef(resolved);
  // 当前显示的片段、是否在播放，以及逐帧回报的 requestAnimationFrame 编号；下面的函数只读写这些 ref，不依赖某次渲染。
  const state = useRef({ index: 0, playing: false, frame: undefined as number | undefined });
  const [active, setActive] = useState(0);
  callbacks.current = { onTime, onReady, onError, onPlayingChange, onEnd };
  timeline.current = resolved;

  /** 当前片段内的播放位置换算成全局时间，不超过该段结尾。 */
  const position = () => {
    const clip = timeline.current.clips[state.current.index];
    const element = videos.current[state.current.index];
    return clip && element ? clip.start + Math.min(element.currentTime, clip.end - clip.start) : 0;
  };

  /** 显示第 index 段并定位到段内 offset 秒，暂停之前显示的视频。 */
  const show = (index: number, offset: number) => {
    if (state.current.index !== index) videos.current[state.current.index]?.pause();
    state.current.index = index;
    setActive(index);
    const element = videos.current[index];
    if (element) element.currentTime = offset;
  };

  /** 停止逐帧回报并暂停当前视频；notify 时通知主控件已暂停。 */
  const stop = (notify: boolean) => {
    if (state.current.frame !== undefined) cancelAnimationFrame(state.current.frame);
    state.current.frame = undefined;
    videos.current[state.current.index]?.pause();
    if (state.current.playing && notify) callbacks.current.onPlayingChange(false);
    state.current.playing = false;
  };

  /** 播放当前段；浏览器拒绝播放（如自动播放限制）时回到暂停状态。 */
  const resume = () => {
    const index = state.current.index;
    void videos.current[index]?.play().catch(() => {
      if (state.current.playing && state.current.index === index) stop(true);
    });
  };

  /** 逐帧回报播放位置：到段尾时衔接下一段，最后一段结束时停在总时长。 */
  const tick = () => {
    state.current.frame = undefined;
    if (!state.current.playing) return;
    const { clips, duration } = timeline.current;
    const clip = clips[state.current.index];
    const element = videos.current[state.current.index];
    if (!clip || !element) return;
    if (element.ended || element.currentTime >= clip.end - clip.start - 0.001) {
      if (state.current.index + 1 >= clips.length) {
        stop(false);
        callbacks.current.onTime(duration);
        callbacks.current.onEnd();
        return;
      }
      show(state.current.index + 1, 0);
      resume();
      callbacks.current.onTime(clips[state.current.index].start);
    } else callbacks.current.onTime(clip.start + element.currentTime);
    state.current.frame = requestAnimationFrame(tick);
  };

  /** 暂停并定位到全局时间 time。 */
  const seek = (time: number) => {
    if (!Number.isFinite(time)) return;
    stop(true);
    const { clips, duration } = timeline.current;
    const target = Math.min(duration, Math.max(0, time));
    const index = clipAt(clips, target);
    show(index, Math.min(target - clips[index].start, clips[index].end - clips[index].start));
    callbacks.current.onTime(target);
  };

  useImperativeHandle(ref, () => ({
    play(from) {
      const start = from ?? (position() >= timeline.current.duration - 0.001 ? 0 : undefined);
      if (start !== undefined) seek(start);
      if (state.current.playing) return;
      state.current.playing = true;
      callbacks.current.onPlayingChange(true);
      resume();
      state.current.frame = requestAnimationFrame(tick);
    },
    pause: () => stop(true),
    seek,
  }), []);

  // 更换媒体、排列或时间规则时停在开头，与 IMS 预览每次修改回到开头一致。
  useEffect(() => {
    stop(true);
    show(0, 0);
    callbacks.current.onTime(0);
  }, [media, order, resolved]);

  useEffect(() => () => stop(false), []);

  return <>{resolved.clips.map((_, index) => {
    const source = sources[order[index]];
    if (!source) return null;
    const shown = index === active;
    return <video key={index} ref={(element) => { videos.current[index] = element; }} src={source.url}
      aria-label={shown ? "基础视频预览" : undefined} aria-hidden={shown ? undefined : true} muted playsInline preload="auto"
      className={`absolute inset-0 size-full bg-black object-contain ${shown ? "" : "invisible"}`}
      onLoadedMetadata={index === 0 ? () => callbacks.current.onReady() : undefined}
      onError={() => callbacks.current.onError()} />;
  })}</>;
}
