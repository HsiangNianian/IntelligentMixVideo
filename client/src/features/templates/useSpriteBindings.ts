/** 云端模板的 Sprite 绑定与资产目录状态：读取、本地编辑、脏检查与整体保存；取消和卸载不会写入过期结果。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getBindings, listSprites, saveBindings } from "@/features/sprites/api";
import type { Placement, SpriteAsset } from "@/features/sprites/model";
import type { Environment } from "./api";

type Status = "unavailable" | "loading" | "ready" | "error";

/** `session` 在每次打开模板或新建草稿时递增，保证同为空 ID 的新草稿也会清空旧绑定。 */
export function useSpriteBindings(session: number, environment: Environment, templateId: string | null) {
  const [status, setStatus] = useState<Status>("unavailable");
  const [message, setMessage] = useState("");
  const [placements, setPlacements] = useState<Placement[]>([]);
  const [revision, setRevision] = useState(0n);
  const [baseline, setBaseline] = useState("[]");
  const [attempt, setAttempt] = useState(0);
  const adopted = useRef<string | null>(null);

  const [assets, setAssets] = useState<SpriteAsset[] | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [catalogWanted, setCatalogWanted] = useState(false);
  const [catalogAttempt, setCatalogAttempt] = useState(0);

  // 云端以外没有绑定；新草稿从空开始；已保存模板读取最新绑定后才允许编辑。
  useEffect(() => {
    if (adopted.current !== null && adopted.current === templateId) {
      adopted.current = null;
      return;
    }
    setPlacements([]);
    setRevision(0n);
    setBaseline("[]");
    setMessage("");
    if (environment !== "cloud") return setStatus("unavailable");
    if (!templateId) return setStatus("ready");
    const controller = new AbortController();
    setStatus("loading");
    getBindings(templateId, controller.signal).then((bindings) => {
      setPlacements(bindings.placements);
      setRevision(bindings.revision);
      setBaseline(JSON.stringify(bindings.placements));
      setStatus("ready");
    }, (reason) => {
      if (controller.signal.aborted) return;
      setMessage(reason instanceof Error ? reason.message : "Sprite 绑定读取失败");
      setStatus("error");
    });
    return () => controller.abort();
  }, [session, environment, templateId, attempt]);

  // 目录只在需要时读取：用户展开资产区，或模板已有绑定需要显示名称与参数。
  const needCatalog = catalogWanted || placements.length > 0;
  useEffect(() => {
    if (environment !== "cloud" || !needCatalog) return;
    const controller = new AbortController();
    setCatalogError("");
    listSprites(controller.signal).then(setAssets, (reason) => {
      if (!controller.signal.aborted) setCatalogError(reason instanceof Error ? reason.message : "Remotion 资产读取失败");
    });
    return () => controller.abort();
  }, [environment, catalogAttempt, needCatalog]);

  /** 展开资产区时刷新，让刚在字效工作区保存的资产出现。 */
  const refreshCatalog = useCallback(() => { setCatalogWanted(true); setCatalogAttempt((value) => value + 1); }, []);

  /** 整体保存绑定；新模板刚获得 ID 时认领该 ID，避免随后重新读取覆盖本次结果。 */
  const save = useCallback(async (id: string) => {
    const saved = await saveBindings(id, placements, revision);
    if (id !== templateId) adopted.current = id;
    setPlacements(saved.placements);
    setRevision(saved.revision);
    setBaseline(JSON.stringify(saved.placements));
  }, [placements, revision, templateId]);

  return {
    status, message, placements, setPlacements, assets, catalogError, refreshCatalog, save,
    dirty: status === "ready" && JSON.stringify(placements) !== baseline,
    retry: () => setAttempt((value) => value + 1),
  };
}
