/** 历史侧栏独立读取分页元数据，聚焦窗口时刷新；卸载或新查询时清理旧请求。 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import type { WorkPage } from "./model";

/** 不为每条历史会话开启连接；当前会话事件、窗口聚焦和用户刷新更新列表。 */
export function useWorkHistory() {
  const [page, setPage] = useState<WorkPage>({ items: [], next_cursor: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const scope = useRef<AbortController | null>(null);
  const alive = useRef(true);
  /** 首次加载替换列表，分页按 ID 合并；新请求中断旧请求，失败保留已有条目。 */
  const load = useCallback(async (cursor?: string) => {
    scope.current?.abort();
    const controller = new AbortController();
    scope.current = controller;
    setLoading(true);
    try {
      const next = await api.history(cursor, controller.signal);
      if (!controller.signal.aborted) {
        setPage((previous) => ({
          items: cursor
            ? [
                ...new Map(
                  [...previous.items, ...next.items].map((work) => [
                    work.id,
                    work,
                  ]),
                ).values(),
              ]
            : next.items,
          next_cursor: next.next_cursor,
        }));
        setError("");
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof Error ? error.message : "历史加载失败。");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  const refresh = useCallback(() => {
    void load();
  }, [load]);
  useEffect(() => {
    alive.current = true;
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      alive.current = false;
      scope.current?.abort();
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);
  return {
    ...page,
    loading,
    error,
    refresh,
    // 删除回执可能晚于页面卸载；淘汰旧请求后重取分页，不能在卸载后开启读取。
    remove: (id: string) => {
      if (!alive.current) return;
      scope.current?.abort();
      setPage((previous) => ({
        ...previous,
        items: previous.items.filter((work) => work.id !== id),
      }));
      refresh();
    },
    more: () => {
      if (page.next_cursor && !loading) void load(page.next_cursor);
    },
  };
}
