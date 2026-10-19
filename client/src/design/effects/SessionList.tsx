/**
 * AI 字效的会话列表，形式参考 Claude 与 ChatGPT 的侧栏：顶部「新对话」，下方按今天 / 昨天 / 更早分组，每个会话一行标题。
 * 只在需要注意时于行尾显示状态（生成中转圈、等待补充、失败）；悬停时状态换成删除按钮。
 * 正在清理的会话变灰、不能打开，行尾常驻「重试删除」。
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Icon } from "../icons";
import { IconButton, Spinner, Tooltip } from "../ui";
import type { Session } from "./model";

interface SessionListProps {
  sessions: Session[];
  currentId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}

/** 会话列表本体。 */
export function SessionList({ sessions, currentId, onSelect, onCreate, onDelete }: SessionListProps) {
  return (
    <aside aria-label="会话" className="flex w-[232px] shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex h-14 shrink-0 items-center px-2.5">
        <button type="button" onClick={onCreate} className="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] font-medium text-fg transition-colors hover:bg-raised [&_svg]:size-[18px]">
          <Icon name="pen-new-round" className="text-accent" />
          新对话
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3">
        {(["今天", "昨天", "更早"] as const).map((group) => {
          const rows = sessions.filter((session) => session.group === group);
          if (!rows.length) return null;
          return (
            <div key={group} className="mt-3 first:mt-1">
              <p className="px-2.5 pb-1 text-[11px] font-medium text-fg-subtle">{group}</p>
              {rows.map((session) => {
                const deleting = session.status === "deleting";
                const active = session.id === currentId;
                return (
                  <div key={session.id} className={cn("group relative flex h-9 items-center rounded-lg transition-colors", active ? "bg-raised" : "hover:bg-raised/70")}>
                    <button type="button" disabled={deleting} onClick={() => onSelect(session.id)} className="flex h-full min-w-0 flex-1 items-center pl-2.5 pr-9 text-left">
                      <span className={cn("truncate text-[13px]", active ? "font-medium text-fg" : deleting ? "text-fg-subtle" : "text-fg-muted group-hover:text-fg")}>{session.title}</span>
                      {deleting && <span className="ml-1.5 shrink-0 text-[11px] text-fg-subtle">· 清理中</span>}
                    </button>
                    <span className="absolute right-1 flex size-7 items-center justify-center">
                      {deleting ? (
                        <IconButton label="重试删除" className="size-7" onClick={() => onDelete(session.id)}>
                          <Icon name="restart" />
                        </IconButton>
                      ) : (
                        <>
                          <span className="group-hover:hidden group-focus-within:hidden"><StatusMark status={session.status} /></span>
                          <IconButton label="删除会话" className="hidden size-7 hover:bg-danger/10 hover:text-danger group-hover:inline-flex group-focus-within:inline-flex" onClick={() => onDelete(session.id)}>
                            <Icon name="trash-bin-minimalistic" />
                          </IconButton>
                        </>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
      <p className="flex items-center gap-1.5 border-t border-line px-4 py-2.5 text-[11px] text-fg-subtle">
        <Icon name="laptop-minimalistic" className="size-3.5" />
        聊天记录保存在这台电脑上
      </p>
    </aside>
  );
}

/** 状态标记：只在需要注意时显示，已完成的会话不加标记。 */
function StatusMark({ status }: { status: Session["status"] }) {
  const marks: Partial<Record<Session["status"], { node: ReactNode; label: string }>> = {
    running: { node: <Spinner className="size-3.5 text-accent" />, label: "生成中" },
    queued: { node: <Spinner className="size-3.5 text-fg-subtle" />, label: "排队中" },
    clarify: { node: <span className="size-2 rounded-full bg-warning" />, label: "等待你补充" },
    failed: { node: <span className="size-2 rounded-full bg-danger" />, label: "生成失败" },
  };
  const mark = marks[status];
  if (!mark) return null;
  return (
    <Tooltip label={mark.label} side="right">
      <span className="flex size-7 items-center justify-center">{mark.node}</span>
    </Tooltip>
  );
}
