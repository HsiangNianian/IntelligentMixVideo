/**
 * 主页：页头、创作入口与统一模板网格；通过标题栏页签筛选全部、云端或本地模板。
 * 页面只负责排版与把用户操作交给上层；数据、加载状态与对话框由业务容器持有。
 */
import { cn } from "@/lib/utils";
import type { Library } from "../types";
import { CreateCards } from "./CreateCards";
import { type SectionState } from "./TemplateSection";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { IconButton } from "../ui";
import { Icon } from "../icons";

export type TemplateFilter = "all" | Library;

interface HomePageProps {
  desktop: boolean;
  cloudState: SectionState;
  onCreate: (library: Library) => void;
  onOpenEffects: () => void;
  onOpenSettings: () => void;
  filter: TemplateFilter;
  onFilter: (filter: TemplateFilter) => void;
  count: number;
  refreshing: boolean;
  onRefresh: () => void;
  children: React.ReactNode;
}

/** 主页内容最宽 1400px，居中；窄窗口时边距收窄。 */
export function HomePage(props: HomePageProps) {
  const { desktop, cloudState, onCreate, onOpenEffects, onOpenSettings, children } = props;
  return (
    <div className="mx-auto w-full max-w-[1400px] px-6 pb-20 pt-9 sm:px-10">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-[26px] font-semibold leading-tight tracking-tight">开始创作</h1>
          <p className="mt-1.5 text-sm text-fg-muted">从模板开始设计视频的样子，或者让 AI 帮你做一段字效。</p>
        </div>
        <ServiceChip state={cloudState} onClick={onOpenSettings} />
      </header>

      <div className="mt-7">
        <CreateCards desktop={desktop} onCreateCloud={() => onCreate("cloud")} onCreateLocal={() => onCreate("local")} onOpenEffects={onOpenEffects} />
      </div>

      <Tabs value={props.filter} onValueChange={(value) => props.onFilter(value as TemplateFilter)} className="mt-14 gap-0">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-line">
          <div className="flex items-center gap-2 py-3">
            <h2 className="text-lg font-semibold tracking-tight">我的模板</h2>
            <span aria-label="模板数量" className="rounded-full bg-raised px-2 py-px font-mono text-[11px] tabular-nums text-fg-muted">{props.count}</span>
          </div>
          <TabsList variant="line" aria-label="模板范围" className="gap-4 p-0">
            {([['all', '全部模板'], ['cloud', '云端模板'], ['local', '本地模板']] as const).map(([value, label]) => (
              <TabsTrigger key={value} value={value} className="rounded-none px-1 after:bottom-[-8px] after:bg-brand data-[state=active]:text-fg">{label}</TabsTrigger>
            ))}
          </TabsList>
          <IconButton label="刷新模板" className="ml-auto" disabled={props.refreshing || (!desktop && props.filter === "local")} onClick={props.onRefresh}>
            <Icon name="restart" className={cn(props.refreshing && "animate-spin")} />
          </IconButton>
        </div>
        <TabsContent key={props.filter} value={props.filter} className="pt-5">{children}</TabsContent>
      </Tabs>
    </div>
  );
}

/** 服务状态胶囊：由云端模板的读取结果推断连接情况，点击打开设置修改服务地址；不额外发起探测请求。 */
function ServiceChip({ state, onClick }: { state: SectionState; onClick: () => void }) {
  const status = {
    ready: { dot: "bg-success", label: "已连接" },
    loading: { dot: "bg-brand animate-pulse", label: "连接中" },
    error: { dot: "bg-danger", label: "连接失败" },
    unavailable: { dot: "bg-fg-subtle", label: "未连接" },
  }[state];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-label={`服务${status.label}，点击打开设置`}
      className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface pl-3 pr-3.5 text-xs text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
    >
      <span className={cn("size-1.5 rounded-full", status.dot)} />
      <span>{status.label}</span>
    </button>
  );
}
