/** 资产浏览的共享骨架：可选的分类切换、搜索、单列资产行与分批显示；IMS 特效目录与 AI 资产目录各自独立使用，只提供各自的条目、封面和选择动作。 */
import { useId, useState, type ReactNode } from "react";
import { Check, Image, Plus, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/** 每批显示的资产数；切换分类或搜索条件后回到第一批。 */
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

/** 一行资产的内容；封面可以是节点，也可以按悬停/聚焦状态渲染（Remotion 封面据此轮播定格帧）。 */
export interface AssetCard {
  /** 稳定的列表键；内容变化需要重新挂载封面时由调用方改变它。 */
  id: string;
  name: string;
  /** 卡片按钮的无障碍名称，也是测试与读屏使用的操作说明。 */
  label: string;
  cover: ReactNode | ((active: boolean) => ReactNode);
  /** 缺省表示条目可重复添加，不显示选中态；布尔值同时决定对勾与 `aria-pressed`。 */
  selected?: boolean;
  disabled?: boolean;
  onSelect: () => void;
  /** 行右侧的操作按钮（如重命名、删除）；它们是选择按钮的兄弟节点，避免按钮嵌套。 */
  actions?: ReactNode;
  /** 名称下方的附加说明。 */
  note?: ReactNode;
}

/** 单行资产：跟踪指针悬停与键盘聚焦（含操作按钮），把“正在被查看”的状态交给封面。 */
function Card({ card }: { card: AssetCard }) {
  const [active, setActive] = useState(false);
  const { name, label, cover, selected, disabled, onSelect, actions, note } = card;
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
        className={cn("template-asset-card h-auto min-h-16 w-full min-w-0 justify-start gap-2 whitespace-normal rounded-lg border p-1.5 text-left", actions && "pb-8", selected && "is-selected")}
        aria-label={label}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onSelect}
      >
        <span className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded bg-muted">{typeof cover === "function" ? cover(active) : cover}</span>
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-[11px] font-medium leading-4">{name}</span>
          {note}
        </span>
        {!actions && (selected ? <Check className="size-3 shrink-0" aria-hidden="true" /> : <Plus className="size-3 shrink-0" aria-hidden="true" />)}
      </Button>
      {actions && <div className="absolute bottom-1 right-2 flex gap-0.5">{actions}</div>}
    </div>
  );
}

/**
 * 名称搜索始终在顶部；提供分类时于左侧显示纵向单选组（再次点击当前分类不会取消选择），右侧显示附加控件和分批资产行。
 * 分类由调用方持有，`items` 只包含当前分类的条目；搜索词与显示批次留在组件内。
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
      <div className="flex min-w-0 items-start gap-2">
        {categories && (
          <ToggleGroup type="single" spacing={1} value={category} onValueChange={(value) => { if (value) { onCategory?.(value); setLimit(PAGE_SIZE); } }} aria-label={categoriesLabel} className="template-asset-categories flex max-h-96 w-[60px] shrink-0 flex-col items-stretch justify-start overflow-y-auto @min-[1000px]:max-h-[65dvh]">
            {categories.map((item) => (
              <ToggleGroupItem key={item.value} value={item.value} className="template-category-pill min-h-7 w-full justify-between gap-1 rounded-md border px-1.5 text-left text-[10px] font-normal">
                <span className="min-w-0 truncate">{item.label}</span>{" "}<span aria-hidden="true" className="template-category-count sr-only">{item.count}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        )}
        <div className="min-w-0 flex-1 space-y-2">
          {controls}
          <div className="max-h-96 overflow-y-auto pr-1 @min-[1000px]:max-h-[65dvh]">
            <div className="flex flex-col gap-1.5">
              {options.slice(0, limit).map((item) => {
                const value = card(item);
                return <Card key={value.id} card={value} />;
              })}
            </div>
            {!options.length && <Empty className="p-6 md:p-6"><EmptyHeader><EmptyMedia variant="icon"><SearchX className="size-5" aria-hidden="true" /></EmptyMedia><EmptyDescription role="status" className="text-xs">{query ? `没有匹配的${noun}` : `${categories ? "此分类" : ""}暂无${noun}`}</EmptyDescription></EmptyHeader></Empty>}
            {options.length > limit && <Button type="button" variant="outline" className="mt-2 h-8 w-full text-[11px]" onClick={() => setLimit((value) => value + PAGE_SIZE)}>显示更多</Button>}
          </div>
        </div>
      </div>
    </>
  );
}
