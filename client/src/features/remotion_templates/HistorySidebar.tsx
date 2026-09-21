/** 历史会话列表和小屏抽屉复用同一内容，业务数据与分页请求由工作区提供。 */
import { useRef, useState } from "react";
import { History, MessageSquare, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogFooter,
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
  onNew: () => void;
  dirty: boolean;
  onDelete: (id: string) => Promise<void>;
}
/** 桌面常驻列表，小屏用带焦点管理和 Escape 关闭能力的抽屉。 */
export function HistorySidebar(props: Props) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<WorkSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const submitted = useRef(false);
  /** 打开确认不修改会话；先关闭窄屏抽屉，避免两个焦点陷阱重叠。 */
  function askDelete(work: WorkSummary) {
    setOpen(false);
    setError("");
    setTarget(work);
  }
  /** 失败保留目标供显式重试，关闭弹窗不会自动再次发送删除。 */
  async function confirmDelete() {
    if (!target || submitted.current) return;
    submitted.current = true;
    setDeleting(true);
    setError("");
    try {
      await props.onDelete(target.id);
      setTarget(null);
    } catch {
      setError(
        "删除未确认完成，清理中的会话无法继续编辑。请重试删除；已开始的清理会在服务重启后继续。",
      );
    } finally {
      submitted.current = false;
      setDeleting(false);
    }
  }
  return (
    <>
      <aside
        aria-label="字效历史会话"
        className="hidden h-full min-h-0 lg:block"
      >
        <HistoryList {...props} onDelete={askDelete} />
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
                onDelete={askDelete}
                onNew={() => {
                  props.onNew();
                  setOpen(false);
                }}
                onSelect={(id) => {
                  props.onSelect(id);
                  setOpen(false);
                }}
              />
            </div>
          </DialogContent>
        </Dialog>
      </div>
      <Dialog
        open={!!target}
        onOpenChange={(value) => {
          if (!value && !submitted.current) setTarget(null);
        }}
      >
        <DialogContent showCloseButton={!deleting}>
          <DialogTitle>删除字效聊天</DialogTitle>
          <DialogDescription>
            将永久删除「{target?.title}
            」的全部聊天、历史代码和生成文件，无法恢复。
            {target &&
              ["queued", "running"].includes(target.job.status) &&
              "正在进行的任务会先停止。"}
            {target?.id === props.selected &&
              props.dirty &&
              "当前未保存的参数修改也会被放弃。"}
          </DialogDescription>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={deleting}
              onClick={() => setTarget(null)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={deleting}
              onClick={() => void confirmDelete()}
            >
              {deleting
                ? "正在停止并清理…"
                : error || target?.deleting
                  ? "重试删除"
                  : target && ["queued", "running"].includes(target.job.status)
                    ? "停止并删除"
                    : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
  onNew,
  onDelete,
}: Omit<Props, "onDelete"> & { onDelete: (work: WorkSummary) => void }) {
  return (
    <section className="flex h-full min-h-0 flex-col rounded-xl border bg-muted/35">
      <div className="flex h-14 shrink-0 items-center justify-between px-4">
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
      <div className="px-3 pb-3">
        <Button
          className="w-full justify-start gap-2 shadow-none"
          onClick={onNew}
        >
          <Plus className="size-4" />
          新增聊天
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
          <div key={work.id} className="flex items-center gap-1">
            <button
              disabled={work.deleting}
              type="button"
              aria-current={selected === work.id ? "true" : undefined}
              title={work.id}
              onClick={() => onSelect(work.id)}
              className={cn(
                "min-w-0 flex-1 space-y-2 rounded-lg border border-transparent p-3 text-left text-sm transition-colors hover:bg-background/80 focus-visible:outline-2 focus-visible:outline-ring",
                selected === work.id &&
                  "border-primary/15 bg-background shadow-sm",
              )}
            >
              <span className="flex items-center gap-2 font-medium">
                <MessageSquare
                  className={cn(
                    "size-3.5 shrink-0",
                    selected === work.id
                      ? "text-primary"
                      : "text-muted-foreground",
                  )}
                />
                <span className="truncate">{work.title}</span>
              </span>
              <span className="flex flex-wrap justify-between gap-1 text-xs text-muted-foreground">
                <span>
                  {work.deleting
                    ? "等待清理 · 可重试删除"
                    : jobLabel(work.job.status)}
                </span>
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
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0 text-muted-foreground hover:text-destructive"
              aria-label="删除聊天"
              title={`删除「${work.title}」`}
              onClick={() => onDelete(work)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
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
