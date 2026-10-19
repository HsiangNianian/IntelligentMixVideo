/**
 * IntelligentMixVideo 标志：品牌渐变的圆角底板上，一个播放三角被横切成三条，中间一条向右错开一点，
 * 像时间线上错落叠放的三段素材合成一个播放键，与开场「三段素材，混剪成片」的故事一致。
 * 各部分带固定类名（logo-tile、logo-strip-1~3），开场动画据此单独驱动；颜色取自主题令牌 brand-from / brand-to。
 */
import { useId } from "react";

/** 播放三角：圆角由同色描边的圆角连接产生。 */
const triangle = "M11.6 8.6v14.8L24 16z";
/** 三条横带的上下边界（视图框单位），相邻两条之间留 1.6 的缝；中间一条向右错开 1.4。 */
const bands = [
  [0, 12.4],
  [14, 18],
  [19.6, 32],
] as const;
const offsets = [0, 1.4, 0];

/** 标志本体；同一页面出现多次时用 useId 区分渐变与裁剪的 id。 */
export function LogoMark({ className }: { className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs>
        <linearGradient id={`${id}-tile`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--color-brand-from)" }} />
          <stop offset="1" style={{ stopColor: "var(--color-brand-to)" }} />
        </linearGradient>
        <linearGradient id={`${id}-gloss`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.28" />
          <stop offset="0.55" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        {bands.map(([top, bottom], index) => (
          <clipPath key={index} id={`${id}-band-${index}`}>
            <rect x="0" y={top} width="32" height={bottom - top} />
          </clipPath>
        ))}
      </defs>
      <g className="logo-tile">
        <rect width="32" height="32" rx="9" fill={`url(#${id}-tile)`} />
        <rect width="32" height="32" rx="9" fill={`url(#${id}-gloss)`} />
        <rect x="0.5" y="0.5" width="31" height="31" rx="8.5" fill="none" stroke="#fff" strokeOpacity="0.2" />
      </g>
      {/* 外层 g 留给开场动画驱动位移，错开量写在内层，二者互不覆盖。 */}
      {bands.map((_, index) => (
        <g key={index} className={`logo-strip-${index + 1}`}>
          <g transform={`translate(${offsets[index]} 0)`}>
            <path d={triangle} clipPath={`url(#${id}-band-${index})`} fill="#fff" stroke="#fff" strokeWidth="2.6" strokeLinejoin="round" />
          </g>
        </g>
      ))}
    </svg>
  );
}
