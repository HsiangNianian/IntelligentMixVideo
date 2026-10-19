/**
 * 编辑页左栏「素材」：顶部切换 IMS 特效与 AI 资产；特效按「文字」「画面」两组分类，可搜索，文字类可选作用对象。
 * 每类素材有各自的缩略预览（花字样式、气泡、滤镜色调、特效光点、转场方向、文字动画），已应用到当前对象的素材标记勾选。
 * 点击素材即应用，由编辑页更新草稿并选中对应对象。
 */
import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Segmented } from "../controls";
import { Icon } from "../icons";
import { Tooltip } from "../ui";
import { categories, flowerStyle, footage, objectMeta, sprites, type CategoryId, type EffectItem, type TextKind } from "./model";

interface AssetPanelProps {
  tab: "ims" | "ai";
  onTab: (tab: "ims" | "ai") => void;
  category: CategoryId;
  onCategory: (category: CategoryId) => void;
  target: TextKind;
  onTarget: (target: TextKind) => void;
  /** 判断素材是否已应用到当前作用对象。 */
  isApplied: (category: CategoryId, item: EffectItem) => boolean;
  onApply: (category: CategoryId, item: EffectItem) => void;
  onAddSprite: (name: string) => void;
}

/** 左栏本体。 */
export function AssetPanel({ tab, onTab, category, onCategory, target, onTarget, isApplied, onApply, onAddSprite }: AssetPanelProps) {
  const [query, setQuery] = useState("");
  const current = categories.find((item) => item.id === category)!;
  const items = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return keyword ? current.items.filter((item) => item.name.toLowerCase().includes(keyword) || item.code.toLowerCase().includes(keyword)) : current.items;
  }, [current, query]);
  const total = categories.reduce((sum, item) => sum + item.items.length, 0);

  return (
    <aside aria-label="素材" className="flex w-[284px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="space-y-3 border-b border-line p-3">
        <Segmented
          label="素材来源"
          value={tab}
          onChange={onTab}
          options={[
            { value: "ims", label: <>特效 <span className="font-mono text-[10.5px] text-fg-subtle">{total}</span></> },
            { value: "ai", label: <>AI 资产 <span className="font-mono text-[10.5px] text-fg-subtle">{sprites.length}</span></> },
          ]}
        />
        {tab === "ims" && (
          <>
            {(["文字", "画面"] as const).map((group) => (
              <div key={group} className="flex items-center gap-1.5">
                <span className="w-7 shrink-0 text-[11px] text-fg-subtle">{group}</span>
                <div className="flex flex-wrap gap-0.5">
                  {categories.filter((item) => item.group === group).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        onCategory(item.id);
                        setQuery("");
                      }}
                      className={cn("h-7 rounded-full px-2 text-xs transition-colors", item.id === category ? "bg-accent/12 font-medium text-accent" : "text-fg-muted hover:bg-raised hover:text-fg")}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            <label className="flex h-8 items-center gap-2 rounded-lg border border-line bg-sunken px-2.5 focus-within:border-accent/60 focus-within:ring-[3px] focus-within:ring-accent/15">
              <Icon name="magnifier" className="size-3.5 shrink-0 text-fg-subtle" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={`搜索 ${current.items.length} 个${current.label}`}
                className="min-w-0 flex-1 bg-transparent text-xs text-fg outline-none placeholder:text-fg-subtle"
              />
            </label>
            {current.targets && (
              <div className="flex items-center gap-2">
                <span className="shrink-0 text-[11px] text-fg-subtle">应用到</span>
                <Segmented
                  size="sm"
                  className="flex-1"
                  label="应用到"
                  value={current.targets.includes(target) ? target : current.targets[0]}
                  onChange={onTarget}
                  options={current.targets.map((kind) => ({ value: kind, label: objectMeta[kind].label }))}
                />
              </div>
            )}
          </>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "ims" ? (
          items.length ? (
            <div className="grid grid-cols-2 gap-2.5">
              {items.map((item, index) => (
                <AssetTile key={item.id} name={item.name} code={item.code} applied={isApplied(category, item)} onClick={() => onApply(category, item)} actionLabel={`应用「${item.name}」`}>
                  <Preview category={category} item={item} index={index} />
                </AssetTile>
              ))}
            </div>
          ) : (
            <p className="py-10 text-center text-xs text-fg-subtle">没有找到「{query}」相关的{current.label}</p>
          )
        ) : (
          <div className="grid grid-cols-2 gap-2.5">
            {sprites.map((sprite) => (
              <AssetTile key={sprite.id} name={sprite.name} code={sprite.kind} applied={false} onClick={() => onAddSprite(sprite.name)} actionLabel={`添加「${sprite.name}」`}>
                <span className="absolute inset-0 flex items-center justify-center" style={{ background: footage[sprite.tone] }}>
                  <span className="grain absolute inset-0 opacity-20 mix-blend-overlay" />
                  <span className="relative px-2 text-center text-[13px] font-bold leading-tight text-white [text-shadow:0_0_12px_oklch(0.8_0.1_265/0.6)]">{sprite.name}</span>
                </span>
              </AssetTile>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

/** 素材卡：上方 4:3 预览，下方名称与编号；已应用时强调色描边加勾，悬停时右下角出现「+」。 */
function AssetTile({ name, code, applied, onClick, actionLabel, children }: { name: string; code: string; applied: boolean; onClick: () => void; actionLabel: string; children: ReactNode }) {
  return (
    <Tooltip label={actionLabel}>
      <button
        type="button"
        onClick={onClick}
        aria-pressed={applied}
        className={cn("group/tile relative overflow-hidden rounded-xl bg-raised text-left ring-1 transition-shadow", applied ? "ring-2 ring-accent" : "ring-line hover:ring-line-strong")}
      >
        <span className="relative block aspect-[4/3] overflow-hidden">
          {children}
          <span className={cn("absolute bottom-1.5 right-1.5 flex size-6 items-center justify-center rounded-full shadow-lift transition-[opacity,transform] duration-200 [&_svg]:size-3.5", applied ? "bg-accent text-accent-ink" : "scale-75 bg-surface text-fg opacity-0 group-hover/tile:scale-100 group-hover/tile:opacity-100")}>
            <Icon name={applied ? "check-circle" : "add-circle"} variant={applied ? "duotone" : "linear"} />
          </span>
        </span>
        <span className="block px-2 pb-1.5 pt-1">
          <span className="block truncate text-xs text-fg">{name}</span>
          <span className="block truncate font-mono text-[10px] text-fg-subtle">{code}</span>
        </span>
      </button>
    </Tooltip>
  );
}

/** 滤镜缩略图的几种色调处理，按序号循环。 */
const filterLooks = ["sepia(0.35) saturate(1.3)", "saturate(1.25) brightness(1.05)", "contrast(1.15) saturate(0.8) sepia(0.2)", "sepia(0.55) hue-rotate(-10deg)", "grayscale(1)", "hue-rotate(25deg) saturate(1.1)", "hue-rotate(-25deg) saturate(1.4)"];

/** 气泡缩略图的底色，按序号循环。 */
const bubbleColors = ["oklch(0.62 0.2 25)", "oklch(0.55 0.2 275)", "oklch(0.7 0.15 150)", "oklch(0.78 0.15 80)", "oklch(0.6 0.18 330)", "oklch(0.25 0.02 265)"];

/** 各类素材的缩略预览，全部用 CSS 画出，示意效果而非真实封面。 */
function Preview({ category, item, index }: { category: CategoryId; item: EffectItem; index: number }) {
  const dark = "linear-gradient(160deg, oklch(0.34 0.04 265), oklch(0.16 0.02 270))";
  switch (category) {
    case "flower":
      return (
        <span className="absolute inset-0 flex items-center justify-center" style={{ background: dark }}>
          <span className="text-[24px] leading-none" style={flowerStyle(item.code)}>花字</span>
        </span>
      );
    case "bubble":
      return (
        <span className="absolute inset-0 flex items-center justify-center bg-hover">
          <span className="relative rounded-2xl px-3 py-1.5 text-[13px] font-bold text-white shadow-lift" style={{ background: bubbleColors[index % bubbleColors.length] }}>
            气泡
            <span className="absolute -bottom-1.5 left-4 size-3 rotate-45 rounded-[2px]" style={{ background: bubbleColors[index % bubbleColors.length] }} />
          </span>
        </span>
      );
    case "filter":
      return <span className="absolute inset-0" style={{ background: footage[index % 3], filter: filterLooks[index % filterLooks.length] }} />;
    case "vfx":
      return (
        <span className="absolute inset-0 overflow-hidden" style={{ background: dark }}>
          {[[22, 30, 1], [64, 22, 0.7], [44, 62, 1.2], [78, 66, 0.8], [30, 76, 0.6]].map(([left, top, scale], spark) => (
            <span
              key={spark}
              className="absolute size-5 animate-twinkle rounded-full"
              style={{ left: `${left}%`, top: `${top}%`, transform: `scale(${scale})`, animationDelay: `${(spark + index) * 0.37}s`, background: `radial-gradient(circle, oklch(0.95 0.06 ${(index * 47) % 360}) 0%, oklch(0.8 0.15 ${(index * 47) % 360} / 0.6) 30%, transparent 70%)` }}
            />
          ))}
        </span>
      );
    case "transition": {
      const directions = ["polygon(0 0, 60% 0, 40% 100%, 0 100%)", "polygon(0 0, 100% 0, 100% 50%, 0 50%)", "circle(38% at 50% 50%)", "polygon(0 0, 50% 0, 50% 100%, 0 100%)"];
      return (
        <span className="absolute inset-0" style={{ background: footage[(index + 1) % 3] }}>
          <span className="absolute inset-0" style={{ background: footage[index % 3], clipPath: directions[index % directions.length] }} />
          <span className="absolute inset-0 flex items-center justify-center text-white/90">
            <Icon name="transfer-horizontal" className="size-5 drop-shadow" />
          </span>
        </span>
      );
    }
    default: {
      // 文字动画：悬停时按入场、出场或循环的方式动一下，静止时显示「Aa」。
      const motion: Record<string, string> = {
        enter: "group-hover/tile:animate-[tile-enter_0.6s_ease-out]",
        exit: "group-hover/tile:animate-[tile-exit_0.6s_ease-in_forwards]",
        loop: "group-hover/tile:animate-[tile-loop_0.9s_ease-in-out_infinite]",
      };
      const style: CSSProperties = { background: dark };
      return (
        <span className="absolute inset-0 flex items-center justify-center" style={style}>
          <span className={cn("text-[26px] font-bold leading-none text-white", motion[category])}>Aa</span>
        </span>
      );
    }
  }
}
