/** 字体名称展示：有预览条时用它作遮罩着色（跟随主题文字色），没有时退回普通文字。 */
import { cn } from "@/lib/utils";

/** 以字体自己的样子显示它的名称；`className` 调整预览条的尺寸。 */
export function FontName({ label, url, className }: { label: string; url: string | null; className?: string }) {
  if (!url) return <span className="truncate">{label}</span>;
  return (
    <span
      role="img"
      aria-label={label}
      className={cn("block h-6 w-44 max-w-full bg-current", className)}
      style={{ maskImage: `url(${url})`, WebkitMaskImage: `url(${url})`, maskRepeat: "no-repeat", WebkitMaskRepeat: "no-repeat", maskSize: "contain", WebkitMaskSize: "contain", maskPosition: "left center", WebkitMaskPosition: "left center" }}
    />
  );
}
