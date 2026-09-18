/** 成功版本目录：恢复历史、合并 SSE 最新结果；请求随作品切换取消，不保存私有候选。 */
import { useEffect, useState } from "react";
import { versions } from "./api";
import type { Version } from "./model";

/** 版本目录读取失败不影响已接收的最新结果，用户可独立重试。 */
export function useTemplateVersions(
  work: string | null,
  current: Version | null,
) {
  const [catalog, setCatalog] = useState<{
    work: string | null;
    items: Version[];
  }>({ work: null, items: [] });
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    if (work)
      void versions(work, controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) {
            if (items.some((item) => item.project_id !== work))
              throw new Error("版本不属于当前会话。");
            setCatalog({ work, items });
          }
        })
        .catch(() => {
          if (!controller.signal.aborted)
            setError("历史版本读取失败，请重试。");
        });
    return () => controller.abort();
  }, [work, current?.id, attempt]);
  const items = new Map(
    (catalog.work === work ? catalog.items : []).map((item) => [item.id, item]),
  );
  if (current?.project_id === work) items.set(current.id, current);
  return {
    versions: [...items.values()].sort((a, b) => a.number - b.number),
    error,
    retry: () => setAttempt((value) => value + 1),
  };
}
