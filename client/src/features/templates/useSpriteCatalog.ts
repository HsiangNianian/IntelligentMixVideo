/** 云端 AI 资产目录状态：展开资产区或模板已有片段时读取，之后随服务端推送与窗口重新聚焦再读；刷新、取消和卸载都不会写入过期结果。 */
import { useCallback, useEffect, useState } from "react";
import { listSprites, watchCatalog } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";

/** 目录只在需要时读取：用户展开资产区，或模板已有片段需要显示名称与时长。 */
export function useSpriteCatalog(hasClips: boolean) {
  const [assets, setAssets] = useState<SpriteAsset[] | null>(null);
  const [error, setError] = useState("");
  const [wanted, setWanted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const needed = wanted || hasClips;

  useEffect(() => {
    if (!needed) return;
    const controller = new AbortController();
    setError("");
    listSprites(controller.signal).then(setAssets, (reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "AI 资产读取失败");
    });
    return () => controller.abort();
  }, [attempt, needed]);

  // 资产库共享，任何人都能保存、改名或删除：目录在用时订阅服务端推送，每次变化重新读取，别人删掉的资产随即从目录消失
  // （否则之后点删除只会得到 404）。代理挡住推送时，切回窗口也会重新读取。
  useEffect(() => {
    if (!needed) return;
    const again = () => setAttempt((value) => value + 1);
    const stop = watchCatalog(again);
    window.addEventListener("focus", again);
    return () => {
      stop();
      window.removeEventListener("focus", again);
    };
  }, [needed]);

  /** 展开资产区时刷新，让刚在字效工作区保存的资产出现。 */
  const refresh = useCallback(() => { setWanted(true); setAttempt((value) => value + 1); }, []);

  return { assets, error, refresh };
}
