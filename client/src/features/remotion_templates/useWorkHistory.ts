/** 历史侧栏读取本地会话库的元数据，聚焦窗口时刷新；卸载或新查询时丢弃旧结果。 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as local from "./localSessions";
import type { WorkSummary } from "./model";

/** 不为每条历史会话开启连接；当前会话事件、窗口聚焦和用户刷新更新列表；本地会话不分页。 */
export function useWorkHistory() {
  const [items, setItems] = useState<WorkSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const scope = useRef<AbortController | null>(null);
  const alive = useRef(true);
  /** 每次读取替换整个列表；新请求中断旧请求，失败保留已有条目。 */
  const refresh = useCallback(() => {
    scope.current?.abort();
    const controller = new AbortController();
    scope.current = controller;
    setLoading(true);
    void local
      .history()
      .then((next) => {
        if (controller.signal.aborted) return;
        setItems(next);
        setError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof Error ? error.message : "历史加载失败。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }, []);
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
    items,
    loading,
    error,
    refresh,
    // 删除回执可能晚于页面卸载；淘汰旧请求后重新读取，不能在卸载后开启读取。
    remove: (id: string) => {
      if (!alive.current) return;
      scope.current?.abort();
      setItems((previous) => previous.filter((work) => work.id !== id));
      refresh();
    },
  };
}
