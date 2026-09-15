/** 将模板草稿转换为 SDK 5.2.2 时间线；从环境配置读取示例视频，组合两个片段及文字轨道。 */
import {
  effectGroups,
  type Draft,
  type EffectAsset,
  type EffectKey,
  type TextRole,
} from "./model";

/** SDK 效果允许混合文字、数值参数；仅从可信目录和经过范围校验的配置生成。 */
type Effect = Record<string, string | number>;

/** 校验数值输入，不让空值、NaN 或越界参数进入 SDK。 */
function number(
  value: number,
  min: number,
  max: number,
  label: string,
  integer = false,
): number {
  if (
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (integer && !Number.isInteger(value))
  ) {
    throw new Error(`${label}须为 ${min}～${max}${integer ? " 的整数" : ""}`);
  }
  return value;
}

/** 生成十秒组合预览，转场重叠量使用已保存时长；空文字不会生成轨道。 */
export function buildTimeline(draft: Draft, catalog: EffectAsset[]) {
  // 空配置回退到原示例；URL 原生解析支持本地路径，避免重复编码已转义的直链。
  const video = new URL(
    import.meta.env.VITE_PREVIEW_VIDEO_URL?.trim() ||
      "https://ice-pub-media.myalicdn.com/vod-demo/最美中国纪录片-智能字幕.mp4",
    window.location.origin,
  ).href;
  const config = draft.editor;
  const byId = new Map(catalog.map((item) => [item.id, item]));
  // 效果类别与字段绑定，拒绝错误类别及未知目录条目。
  const effect = (key: EffectKey): Effect => {
    if (!config[key]) return {};
    const item = byId.get(config[key]);
    if (!item || item.category !== effectGroups[key])
      throw new Error(`效果不在对应目录中：${config[key]}`);
    return { ...item.parameters };
  };
  // SDK 小于 1 才识别为相对坐标，100% 需要限制在边界内。
  const position = (value: number) =>
    Math.min(number(value, 0, 100, "位置") / 100, 0.9999);
  const text = (role: TextRole) => {
    const content = role === "bubble" ? config.bubbleText : config[role];
    const size = number(config[`${role}Size`], 12, 120, "字号", true);
    if (
      config[`${role}Loop`] &&
      (config[`${role}In`] || config[`${role}Out`])
    ) {
      throw new Error("同一类文字的循环动画不能与入场、出场同时使用");
    }
    const motion: Effect = {
      ...effect(`${role}In`),
      ...effect(`${role}Out`),
      ...effect(`${role}Loop`),
    };
    for (const type of ["In", "Out"] as const) {
      const duration = number(
        config[`${role}${type}Duration`],
        0.1,
        3,
        "动画时长",
      );
      if (config[`${role}${type}`]) motion[`AaiMotion${type}`] = duration;
    }
    return {
      Type: "Text",
      Content: role === "bubble" ? content : wrapPreviewText(content, size),
      TimelineIn: 0,
      TimelineOut: 10,
      X: position(config[`${role}X`]),
      Y: position(config[`${role}Y`]),
      Alignment: "Center",
      Font: "Alibaba PuHuiTi",
      FontSize: size,
      FontColor: "#FFFFFF",
      Outline: 0,
      ...effect(role === "bubble" ? "bubble" : `${role}Flower`),
      ...motion,
      ...(role === "bubble" ? { Width: 0.5 } : {}),
    };
  };
  const effects: Effect[] = [{ Type: "Volume", Gain: 0 }];
  if (config.filter) effects.push({ Type: "Filter", ...effect("filter") });
  if (config.vfx) effects.push({ Type: "VFX", ...effect("vfx") });
  const overlap = config.transition
    ? number(draft.transition_duration_seconds, 0.1, 3, "转场时长")
    : 0;
  // 仅转场需要两个视频元素；普通预览复用单一素材，降低逐帧纹理上传开销。
  const clip = (start: number, end: number, timelineIn: number) => ({
    Type: "Video",
    MediaURL: video,
    In: start,
    Out: end,
    TimelineIn: timelineIn,
    TimelineOut: timelineIn + end - start,
    Width: 0.9999,
    Height: 0.9999,
    AdaptMode: "Cover",
    Effects: [...effects],
  });
  let clips;
  if (config.transition) {
    // 片段一在第 5 秒后延伸 overlap 秒，片段二从第 5 秒开始，形成真实重叠。
    const first = clip(0, 5 + overlap, 0);
    first.Effects.push({
      Type: "Transition",
      Duration: overlap,
      ...effect("transition"),
    });
    clips = [first, clip(8, 13, 5)];
  } else {
    clips = [clip(0, 10, 0)];
  }
  const texts = [text("title"), text("subtitle")];
  if (config.bubble) texts.push(text("bubble"));
  return {
    VideoTracks: [{ VideoTrackClips: clips }],
    SubtitleTracks: [
      { SubtitleTrackClips: texts.filter((item) => item.Content.trim()) },
    ],
    AudioTracks: [],
    AspectRatio: "16:9",
    FECanvas: { Width: 800, Height: 450 },
  };
}

/** 按字素折行并保留显式换行；不把 emoji 或组合字符拆开。 */
function wrapPreviewText(content: string, size: number): string {
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return content;
  context.font = `${size}px "ims-preview"`;
  const segmenter = new Intl.Segmenter("zh", { granularity: "grapheme" });
  // ponytail: 测量字体宽度不包含花字外扩；需逐像素一致时改用 SDK 字形测量。
  return content
    .split(/\r\n?|\n/)
    .map((paragraph) => {
      const lines = [""];
      for (const { segment } of segmenter.segment(paragraph)) {
        const last = lines.length - 1;
        if (
          lines[last] &&
          context.measureText(lines[last] + segment).width > 720
        )
          lines.push(segment);
        else lines[last] += segment;
      }
      return lines.join("\n");
    })
    .join("\n");
}
