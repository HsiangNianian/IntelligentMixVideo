/** 主页将真实云端模板接入工作室卡片，读写仍通过既有存储边界。 */
import { useEffect, useState } from "react";
import { Tooltip } from "radix-ui";
import { toast } from "sonner";
import { HomePage as HomeView } from "@/components/studio/home/HomePage";
import { TemplateSection, type SectionState } from "@/components/studio/home/TemplateSection";
import { NewTemplateDialog } from "@/components/studio/home/NewTemplateDialog";
import { DeleteTemplateDialog } from "@/components/studio/home/DeleteTemplateDialog";
import { apiBase } from "@/lib/api-base";
import { deleteTemplate, listTemplates } from "./api";
import type { TemplateSummary } from "./model";
import type { EditingState } from "./TemplateWorkspace";

/** 只创建草稿，编辑器保存时才真正写入存储。 */
export type TemplateSelection =
  | { templateId: string }
  | { templateId: null; name: string; description: string };
interface Props {
  onSelect: (selection: TemplateSelection) => void;
  onDelete?: (selection: TemplateSelection) => void;
}

/** 读取云端模板列表并支持重试；卸载或服务地址变化时取消上一次请求。 */
function useTemplateCollection() {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const address = apiBase();

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void listTemplates(controller.signal)
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
  }, [attempt, address]);

  return { templates, loading, error, refresh: () => setAttempt((value) => value + 1) };
}

type Collection = ReturnType<typeof useTemplateCollection>;

/** 真实读取状态映射为视图状态。 */
function sectionState(collection: Collection): SectionState {
  return collection.loading ? "loading" : collection.error ? "error" : "ready";
}

/** 模板网格与删除确认；删除成功后通知编辑区并重新读取列表。 */
export function TemplateCollection({ collection, onCreate, onSelect, onDelete, editing }: Props & {
  collection: Collection;
  onCreate: () => void;
  editing?: EditingState | null;
}) {
  const [deleting, setDeleting] = useState<TemplateSummary | null>(null);
  return <Tooltip.Provider delayDuration={350}>
    <TemplateSection state={sectionState(collection)} templates={collection.templates} error={collection.error} onRefresh={collection.refresh} onCreate={onCreate}
      editing={editing ? { id: editing.id, dirty: editing.dirty } : null}
      onOpen={(template) => onSelect({ templateId: template.template_id })} onDelete={setDeleting}
      onCopyId={async (template) => { try { await navigator.clipboard.writeText(template.template_id); toast.success("模板 ID 已复制"); } catch { toast.error("复制失败，请稍后重试"); } }} />
    <DeleteTemplateDialog target={deleting} onClose={() => setDeleting(null)}
      onDelete={async (template) => { await deleteTemplate(template.template_id); onDelete?.({ templateId: template.template_id }); collection.refresh(); }} />
  </Tooltip.Provider>;
}

/** 新建入口、服务状态与模板网格共用同一次列表读取。 */
export function TemplateHome({ onSelect, onDelete, onOpenRemotion, onOpenSettings = () => {}, editing }: Props & {
  onOpenRemotion: () => void;
  onOpenSettings?: () => void;
  editing?: EditingState | null;
}) {
  const cloud = useTemplateCollection();
  const [creating, setCreating] = useState(false);
  const state = sectionState(cloud);
  return <>
    <HomeView cloudState={state} onCreate={() => setCreating(true)} onOpenEffects={onOpenRemotion} onOpenSettings={onOpenSettings}
      count={state === "ready" ? cloud.templates.length : 0} refreshing={state === "loading"} onRefresh={cloud.refresh}>
      <TemplateCollection collection={cloud} onCreate={() => setCreating(true)} onSelect={onSelect} onDelete={onDelete} editing={editing} />
    </HomeView>
    <NewTemplateDialog open={creating} existingNames={cloud.templates.map((template) => template.name)} onClose={() => setCreating(false)} onCreate={({ name, description }) => { onSelect({ templateId: null, name, description }); setCreating(false); }} />
  </>;
}
