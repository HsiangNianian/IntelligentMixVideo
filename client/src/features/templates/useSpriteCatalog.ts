/** 云端 AI 资产目录状态：工作区可见时按需读取，返回模板页、服务端推送与窗口重新聚焦时补读；刷新、取消和卸载都不会写入过期结果。 */
import { useCallback, useEffect, useState } from "react";
import { listSprites, watchCatalog } from "@/features/sprites/api";
import type { SpriteAsset } from "@/features/sprites/model";

/** 目录只在需要且工作区可见时读取；主导航保留挂载，返回时补读以恢复漏掉推送的变化，隐藏期间保留已有目录和订阅。 */
export function useSpriteCatalog(hasClips: boolean, active = true) {
  const [assets, setAssets] = useState<SpriteAsset[] | null>(null);
  const [error, setError] = useState("");
  // wanted 表示资产区已展开，read 表示右栏只借这份目录列出字效（读完不订阅推送，也不随推送重读）。
  const [wanted, setWanted] = useState(false);
  const [read, setRead] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const needed = wanted || read || hasClips;

  useEffect(() => {
    if (!needed || !active) return;
    const controller = new AbortController();
    setError("");
    listSprites(controller.signal).then((items) => {
      if (!controller.signal.aborted) setAssets(items);
    }, (reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "AI 资产读取失败");
    });
    return () => controller.abort();
  }, [attempt, needed, active]);

  // 资产库共享，任何人都能保存、改名或删除：目录在用时订阅服务端推送，每次变化重新读取，别人删掉的资产随即从目录消失
  // （否则之后点删除只会得到 404）。代理挡住推送时，切回窗口也会重新读取。
  const watching = wanted || hasClips;
  useEffect(() => {
    if (!watching) return;
    const again = () => setAttempt((value) => value + 1);
    const stop = watchCatalog(again);
    window.addEventListener("focus", again);
    return () => {
      stop();
      window.removeEventListener("focus", again);
    };
  }, [watching]);

  /** 展开资产区时刷新，让刚在字效工作区保存的资产出现。 */
  const refresh = useCallback(() => { setWanted(true); setAttempt((value) => value + 1); }, []);

  /** 右栏选中标题或字幕时读一次目录（「用 Remotion 字效显示」需要它），不订阅推送。 */
  const peek = useCallback(() => setRead(true), []);

  return { assets, error, refresh, peek };
}
