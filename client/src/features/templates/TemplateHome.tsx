/** 主页将真实云端/本地模板接入工作室卡片，读写仍通过既有存储边界。 */
import { useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Tooltip } from "radix-ui";
import { toast } from "sonner";
import { HomePage as HomeView, type TemplateFilter } from "@/components/studio/home/HomePage";
import { TemplateSection, type SectionState } from "@/components/studio/home/TemplateSection";
import { NewTemplateDialog } from "@/components/studio/home/NewTemplateDialog";
import { DeleteTemplateDialog } from "@/components/studio/home/DeleteTemplateDialog";
import { apiBase } from "@/lib/api-base";
import { deleteTemplate, listTemplates, type Environment } from "./api";
import type { TemplateSummary } from "./model";
import type { EditingState } from "./TemplateWorkspace";

/** 只创建草稿，编辑器保存时才真正写入存储。 */
export type TemplateSelection = { environment: Environment } & (
  | { templateId: string }
  | { templateId: null; name: string; description: string }
);
interface Props {
  onSelect: (selection: TemplateSelection) => void;
  onDelete?: (selection: TemplateSelection) => void;
}

/** 两个模板库分别读取和重试；卸载取消 HTTP，并忽略迟到的本地 IPC 结果。 */
function useTemplateCollection(environment: Environment) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const unavailable = environment === "local" && !isTauri();
  const address = apiBase();

  useEffect(() => {
    if (unavailable) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void listTemplates(controller.signal, environment)
      .then((items) => {
        if (!controller.signal.aborted) setTemplates(items);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "模板列表加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [environment, attempt, unavailable, address]);

  return { templates, loading, error, unavailable, refresh: () => setAttempt((value) => value + 1) };
}


/** 真实读取状态映射为视图状态，本地不可用优先于初始加载。 */
function sectionState(collection: ReturnType<typeof useTemplateCollection>): SectionState {
  return collection.unavailable ? "unavailable" : collection.loading ? "loading" : collection.error ? "error" : "ready";
}

/** 当前筛选范围内的库及其读取状态，刷新仍按库独立执行。 */
interface Collection {
  environment: Environment;
  collection: ReturnType<typeof useTemplateCollection>;
  onCreate?: () => void;
}

/** 两库合并展示，打开、删除和编辑状态始终保留所属库，避免同 ID 操作错库。 */
export function TemplateCollection({ collections, onSelect, onDelete, editing }: Props & {
  collections: Collection[];
  editing?: EditingState | null;
}) {
  const [deleting, setDeleting] = useState<{ library: Environment; template: TemplateSummary } | null>(null);
  return <Tooltip.Provider delayDuration={350}>
    <TemplateSection libraries={collections.map(({ environment, collection, onCreate }) => ({ library: environment, state: sectionState(collection), templates: collection.templates, error: collection.error, onRefresh: collection.refresh, onCreate: onCreate ?? (() => {}) }))}
      editing={editing ? { library: editing.environment, id: editing.id, dirty: editing.dirty } : null}
      onOpen={(environment, template) => onSelect({ environment, templateId: template.template_id })} onDelete={(library, template) => setDeleting({ library, template })}
      onCopyId={async (template) => { try { await navigator.clipboard.writeText(template.template_id); toast.success("模板 ID 已复制"); } catch { toast.error("复制失败，请稍后重试"); } }} />
    <DeleteTemplateDialog target={deleting} onClose={() => setDeleting(null)}
      onDelete={async (library, template) => { await deleteTemplate(template.template_id, library, template.library); onDelete?.({ environment: library, templateId: template.template_id }); collections.find(({ environment }) => environment === library)?.collection.refresh(); }} />
  </Tooltip.Provider>;
}

/** 新建入口、服务状态与模板筛选共用实际数据，切换页签复用两个库的读取状态。 */
export function TemplateHome({ onSelect, onDelete, onOpenRemotion, onOpenSettings = () => {}, editing }: Props & {
  onOpenRemotion: () => void;
  onOpenSettings?: () => void;
  editing?: EditingState | null;
}) {
  const cloud = useTemplateCollection("cloud");
  const local = useTemplateCollection("local");
  const [creating, setCreating] = useState<Environment | null>(null);
  const [filter, setFilter] = useState<TemplateFilter>("all");
  const collections: Collection[] = (["cloud", "local"] as const)
    .filter((environment) => filter === "all" ? environment === "cloud" || !local.unavailable : environment === filter)
    .map((environment) => ({ environment, collection: environment === "cloud" ? cloud : local, onCreate: () => setCreating(environment) }));
  return <>
    <HomeView desktop={isTauri()} cloudState={sectionState(cloud)} onCreate={setCreating} onOpenEffects={onOpenRemotion} onOpenSettings={onOpenSettings}
      filter={filter} onFilter={setFilter} count={collections.reduce((sum, { collection }) => sum + (sectionState(collection) === "ready" ? collection.templates.length : 0), 0)}
      refreshing={collections.some(({ collection }) => sectionState(collection) === "loading")} onRefresh={() => collections.forEach(({ collection }) => collection.refresh())}>
      <TemplateCollection collections={collections} onSelect={onSelect} onDelete={onDelete} editing={editing} />
    </HomeView>
    <NewTemplateDialog library={creating} existingNames={(creating === "local" ? local : cloud).templates.map((template) => template.name)} onClose={() => setCreating(null)} onCreate={({ library, name, description }) => { onSelect({ environment: library, templateId: null, name, description }); setCreating(null); }} />
  </>;
}
