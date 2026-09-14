/** 历史会话列表和小屏抽屉复用同一内容，业务数据与分页请求由工作区提供。 */
import { useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { jobLabel, type WorkSummary } from "./model";

/** 列表只负责选择和展示，切换不影响服务端任务。 */
interface Props {
  items: WorkSummary[];
  selected: string | null;
  loading: boolean;
  error: string;
  hasMore: boolean;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  onMore: () => void;
}
/** 桌面常驻列表，小屏用带焦点管理和 Escape 关闭能力的抽屉。 */
export function HistorySidebar(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <aside
        aria-label="字效历史会话"
        className="hidden h-full min-h-0 lg:block"
      >
        <HistoryList {...props} />
      </aside>
      <div className="lg:hidden">
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            props.onRefresh();
            setOpen(true);
          }}
        >
          <History />
          聊天历史
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="top-0 left-0 flex h-dvh max-w-80 translate-x-0 translate-y-0 flex-col rounded-none p-4 sm:max-w-80">
            <DialogTitle>字效聊天历史</DialogTitle>
            <DialogDescription>
              切换会话不会停止正在制作的模板。
            </DialogDescription>
            <div className="min-h-0 flex-1">
              <HistoryList
                {...props}
                onSelect={(id) => {
                  props.onSelect(id);
                  setOpen(false);
                }}
              />
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </>
  );
}
/** 选中项使用 aria-current，分页与失败恢复保持明确操作入口。 */
function HistoryList({
  items,
  selected,
  loading,
  error,
  hasMore,
  onSelect,
  onRefresh,
  onMore,
}: Props) {
  return (
    <section className="flex h-full min-h-0 flex-col rounded-2xl border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-3">
        <h2 className="text-sm font-medium">聊天历史</h2>
        <Button
          variant="ghost"
          size="icon"
          aria-label="刷新历史"
          disabled={loading}
          onClick={onRefresh}
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {error && (
          <p role="alert" className="p-2 text-xs text-destructive">
            {error}
          </p>
        )}
        {!items.length && (
          <p className="p-3 text-xs text-muted-foreground">
            {loading ? "正在读取历史…" : "还没有聊天会话"}
          </p>
        )}
        {items.map((work) => (
          <button
            key={work.id}
            type="button"
            aria-current={selected === work.id ? "true" : undefined}
            title={work.id}
            onClick={() => onSelect(work.id)}
            className={cn(
              "w-full space-y-2 rounded-lg p-3 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring",
              selected === work.id && "bg-muted",
            )}
          >
            <span className="block truncate font-medium">{work.title}</span>
            <span className="flex flex-wrap justify-between gap-1 text-xs text-muted-foreground">
              <span>{jobLabel(work.job.status)}</span>
              <time dateTime={work.updated_at}>
                {new Date(work.updated_at).toLocaleString("zh-CN", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
            </span>
          </button>
        ))}
        {hasMore && (
          <Button
            className="w-full"
            variant="ghost"
            disabled={loading}
            onClick={onMore}
          >
            加载更多会话
          </Button>
        )}
      </div>
    </section>
  );
}
