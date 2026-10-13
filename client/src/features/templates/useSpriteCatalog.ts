/** 云端 Remotion 资产目录状态：展开资产区或模板已有片段时读取，刷新、取消和卸载都不会写入过期结果。 */
import { useCallback, useEffect, useState } from "react";
import { listSprites } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";
import type { Environment } from "./api";

/** 目录只在需要时读取：用户展开资产区，或模板已有片段需要显示名称与时长；本地模板不支持 Remotion 资产。 */
export function useSpriteCatalog(environment: Environment, hasClips: boolean) {
  const [assets, setAssets] = useState<SpriteAsset[] | null>(null);
  const [error, setError] = useState("");
  const [wanted, setWanted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const needed = wanted || hasClips;

  useEffect(() => {
    if (environment !== "cloud" || !needed) return;
    const controller = new AbortController();
    setError("");
    listSprites(controller.signal).then(setAssets, (reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Remotion 资产读取失败");
    });
    return () => controller.abort();
  }, [environment, attempt, needed]);

  /** 展开资产区时刷新，让刚在字效工作区保存的资产出现。 */
  const refresh = useCallback(() => { setWanted(true); setAttempt((value) => value + 1); }, []);

  return { assets, error, refresh };
}
