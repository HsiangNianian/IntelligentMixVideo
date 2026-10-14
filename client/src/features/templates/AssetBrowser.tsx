/** 资产浏览的共享骨架：可选的分类切换、搜索、卡片网格与分批显示；IMS 特效目录与 Remotion 资产目录各自独立使用，只提供各自的条目、封面和选择动作。 */
import { useId, useState, type ReactNode } from "react";
import { Check, Image, Plus, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/** 每批显示的卡片数；切换分类或搜索条件后回到第一批。 */
const PAGE_SIZE = 24;

/** 封面不可用时的明确占位，保留名称和选择入口。 */
export function CoverPlaceholder({ text }: { text: string }) {
  return (
    <span className="flex flex-col items-center gap-1 text-muted-foreground">
      <Image className="size-5" aria-hidden="true" />
      <span className="text-[11px]">{text}</span>
    </span>
  );
}

/** 一张卡片的内容；封面可以是节点，也可以按悬停/聚焦状态渲染（Remotion 封面据此轮播定格帧）。 */
export interface AssetCard {
  /** 稳定的列表键；内容变化需要重新挂载封面时由调用方改变它。 */
  id: string;
  name: string;
  /** 卡片按钮的无障碍名称，也是测试与读屏使用的操作说明。 */
  label: string;
  cover: ReactNode | ((active: boolean) => ReactNode);
  /** 封面区尺寸类，缺省为固定 68px 高；封面自带比例时传入 `h-auto`。 */
  coverClassName?: string;
  /** 缺省表示条目可重复添加，不显示选中态；布尔值同时决定对勾与 `aria-pressed`。 */
  selected?: boolean;
  disabled?: boolean;
  onSelect: () => void;
  /** 叠在封面右上角的操作按钮（如重命名、删除）；它们是选择按钮的兄弟节点，避免按钮嵌套。 */
  actions?: ReactNode;
  /** 卡片下方的附加说明，位于选择按钮之外。 */
  note?: ReactNode;
}

/** 单张卡片：跟踪指针悬停与键盘聚焦（含操作按钮），把“正在被查看”的状态交给封面。 */
function Card({ card }: { card: AssetCard }) {
  const [active, setActive] = useState(false);
  const { name, label, cover, coverClassName, selected, disabled, onSelect, actions, note } = card;
  return (
    <div
      className="relative min-w-0"
      onPointerEnter={() => setActive(true)}
      onPointerLeave={() => setActive(false)}
      onFocus={() => setActive(true)}
      onBlur={() => setActive(false)}
    >
      <Button
        type="button"
        variant="ghost"
        className={cn("template-asset-card h-auto w-full min-w-0 flex-col items-stretch gap-1 whitespace-normal rounded-lg border p-1 text-left", selected && "is-selected")}
        aria-label={label}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onSelect}
      >
        <span className={cn("flex items-center justify-center overflow-hidden rounded bg-muted", coverClassName ?? "h-[68px]")}>{typeof cover === "function" ? cover(active) : cover}</span>
        <span className="flex min-w-0 items-start justify-between gap-0.5">
          <span className="min-w-0 break-all text-[11px] leading-4">{name}</span>
          {selected ? <Check className="size-3 shrink-0" aria-hidden="true" /> : <Plus className="size-3 shrink-0" aria-hidden="true" />}
        </span>
      </Button>
      {actions && <div className="absolute right-1.5 top-1.5 flex gap-0.5">{actions}</div>}
      {note}
    </div>
  );
}

/**
 * 提供分类时显示单选切换组（再次点击当前分类不会取消选择），其下是名称搜索、调用方的附加控件和分批显示的卡片网格；
 * 不提供分类的目录直接从搜索开始。分类由调用方持有，`items` 只包含当前分类的条目；搜索词与显示批次留在组件内。
 */
export function AssetBrowser<T>({ categories, category, onCategory, categoriesLabel, searchLabel, searchPlaceholder, noun, items, searchText, card, controls }: {
  /** 分类相关属性要么全部提供，要么全部省略。 */
  categories?: { value: string; label: string; count: number }[];
  category?: string;
  onCategory?: (value: string) => void;
  categoriesLabel?: string;
  searchLabel: string;
  searchPlaceholder: string;
  /** 空状态文案中的资产称呼，例如“特效”。 */
  noun: string;
  items: T[];
  searchText: (item: T) => string;
  card: (item: T) => AssetCard;
  /** 位于搜索框与网格之间的附加控件，例如作用对象选择与互斥提示。 */
  controls?: ReactNode;
}) {
  const id = useId();
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const query = search.trim().toLocaleLowerCase();
  const options = items.filter((item) => searchText(item).toLocaleLowerCase().includes(query));
  return (
    <>
      {categories && (
        <ToggleGroup type="single" spacing={1} value={category} onValueChange={(value) => { if (value) { onCategory?.(value); setLimit(PAGE_SIZE); } }} aria-label={categoriesLabel} className="template-asset-categories w-full flex-wrap">
          {categories.map((item) => (
            <ToggleGroupItem key={item.value} value={item.value} className="template-category-pill h-6 gap-1 rounded-full border px-2 text-[11px] font-normal">
              {item.label} <span aria-hidden="true" className="template-category-count">{item.count}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${id}-search`} className="sr-only">{searchLabel}</Label>
        <Input
          id={`${id}-search`}
          type="search"
          placeholder={searchPlaceholder}
          value={search}
          onChange={(event) => { setSearch(event.target.value); setLimit(PAGE_SIZE); }}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
          className="h-8 px-2.5 text-[11px] md:text-[11px]"
        />
      </div>
      {controls}
      <div className="max-h-96 overflow-y-auto pr-1 @min-[1000px]:max-h-[65dvh]">
        <div className="grid grid-cols-2 gap-2">
          {options.slice(0, limit).map((item) => {
            const value = card(item);
            return <Card key={value.id} card={value} />;
          })}
        </div>
        {!options.length && <Empty className="p-6 md:p-6"><EmptyHeader><EmptyMedia variant="icon"><SearchX className="size-5" aria-hidden="true" /></EmptyMedia><EmptyDescription role="status" className="text-xs">{query ? `没有匹配的${noun}` : `${categories ? "此分类" : ""}暂无${noun}`}</EmptyDescription></EmptyHeader></Empty>}
        {options.length > limit && <Button type="button" variant="outline" className="mt-2 h-8 w-full text-[11px]" onClick={() => setLimit((value) => value + PAGE_SIZE)}>显示更多</Button>}
      </div>
    </>
  );
}
