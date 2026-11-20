/** 主页复用同一份云端列表，展示最近五项、按名称搜索与每页十五项的网格；写入仍走既有存储边界。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Tooltip } from "radix-ui";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { HomePage as HomeView } from "@/components/studio/home/HomePage";
import { TemplateSection, type SectionState } from "@/components/studio/home/TemplateSection";
import { TemplateCard } from "@/components/studio/home/TemplateCard";
import { Button } from "@/components/ui/button";
import { NewTemplateDialog } from "@/components/studio/home/NewTemplateDialog";
import { DeleteTemplateDialog } from "@/components/studio/home/DeleteTemplateDialog";
import { apiBase } from "@/lib/api-base";
import { CoverRevisionConflict, deleteTemplate, getTemplate, hasCoverBlob, listTemplates, publishCover, readTemplateCover, syncLoadedCovers } from "./api";
import { releaseCoverTab, requestCoverTab } from "./coverCapture";
import { needsInitialCover, type Template, type TemplateSummary } from "./model";
import type { EditingState } from "./TemplateWorkspace";

/** 只创建草稿，编辑器保存时才真正写入存储。 */
export type TemplateSelection =
  | { templateId: string }
  | { templateId: null; name: string; description: string };
interface Props {
  onSelect: (selection: TemplateSelection) => void;
  onDelete?: (selection: TemplateSelection) => void;
}

/** 首次读取后在主页逐张补旧封面；切页暂停，回来复用剩余列表，手动刷新同时修复未发布版本。 */
function useTemplateCollection(revision: number, active: boolean) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [authorize, setAuthorize] = useState<(() => void) | null>(null);
  const running = useRef<AbortController | null>(null);
  const [batch, setBatch] = useState<{ pending: Set<TemplateSummary>; missing: Set<string>; address: string } | null>(null);
  const address = apiBase();
  const previousAddress = useRef(address);

  const load = useCallback(async (manual = false) => {
    running.current?.abort();
    const controller = new AbortController();
    const { signal } = controller;
    running.current = controller;
    setBatch(null);
    setAuthorize(null);
    setRefreshing(manual);
    setError("");
    try {
      const items = await listTemplates(signal);
      if (signal.aborted) return;
      setTemplates(items);
      const missing = manual ? await syncLoadedCovers(items) : new Set<string>();
      if (signal.aborted) return;
      const pending = items.filter(item => !item.validation_error && (needsInitialCover(item) || (manual
        && (missing.has(item.template_id) || !item.cover?.url || item.cover.revision !== item.cover.publishedRevision))));
      setBatch({ pending: new Set(pending), missing, address });
      if (!pending.length) setRefreshing(false);
    } catch (reason) {
      if (!signal.aborted) {
        setError(reason instanceof Error ? reason.message : "模板列表加载失败");
        setRefreshing(false);
      }
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [address]);

  // 列表只随保存、刷新或服务地址变更而重读；离开主页只取消取图。
  useEffect(() => {
    if (!active) {
      setAuthorize(null); setRefreshing(false);
      return;
    }
    if (!batch || batch.address !== address || !batch.pending.size) return;
    const { pending, missing } = batch;
    const controller = new AbortController();
    const { signal } = controller;
    setRefreshing(true);
    const cached: TemplateSummary[] = [], unchecked: TemplateSummary[] = [];
    const awaitingShare: { item: TemplateSummary; template: Template }[] = [];
    const failures: string[] = [];
    for (const item of pending) {
      if (hasCoverBlob(item.template_id, item.cover?.revision ?? item.template_id)) cached.push(item);
      else unchecked.push(item);
    }

    /** 接受详情中比摘要更新的完整发布。已知同版缺图仍应以原素材补截。 */
    function peerCover(item: TemplateSummary, template: Template) {
      const cover = template.cover;
      if (cover?.url && cover.revision === cover.publishedRevision && !needsInitialCover(template)
        && cover.publishedRevision !== item.cover?.publishedRevision) return cover;
    }

    /** 两个陈列区共用同一次读取，切页后保留未完成的模板。 */
    async function display(item: TemplateSummary, cover: { revision: string; url: string; source?: string }) {
      signal.throwIfAborted();
      setTemplates(current => current.map(row => row.template_id === item.template_id ? { ...row, cover: {
        source: cover.source ?? item.cover?.source ?? "builtin", revision: cover.revision, publishedRevision: cover.revision, url: cover.url,
      } } : row));
      pending.delete(item);
      await readTemplateCover(item.template_id, cover.revision);
    }

    /** 重传缓存，或在取帧前核对实际详情；409 只重读冲突的那一个模板。 */
    async function repair(item: TemplateSummary, prepared?: Template, stream?: MediaStream) {
      if (signal.aborted) return;
      const id = item.template_id;
      const version = item.cover?.revision ?? id;
      try {
        let capture: (() => Promise<Blob>) | undefined;
        if (!hasCoverBlob(id, version)) {
          const template = prepared ?? await getTemplate(id, signal);
          signal.throwIfAborted();
          const peer = peerCover(item, template);
          if (peer) { await display(item, peer); return; }
          if ((template.cover?.revision ?? id) !== version) throw new Error("模板已更新，请再次刷新");
          if (template.cover?.source === "unavailable") throw new Error("请在编辑器选择原视频并保存");
          if (!isTauri() && !stream) { awaitingShare.push({ item, template }); return; }
          capture = async () => (await import("./homeCoverCapture")).captureHomeCover(template, signal, stream);
        }
        const refresh = needsInitialCover(item) || (missing.has(id) && version === item.cover?.publishedRevision);
        const cover = await publishCover(id, version, capture, refresh, signal).catch(async error => {
          if (!(error instanceof CoverRevisionConflict)) throw error;
          const peer = peerCover(item, await getTemplate(id, signal));
          if (!peer) throw error;
          return peer;
        });
        await display(item, cover);
      } catch (reason) {
        if (!signal.aborted) {
          pending.delete(item);
          failures.push(item.name + "：" + (reason instanceof Error ? reason.message : "封面更新失败"));
        }
      }
    }

    /** 用户点击后立即申请共享，整批结束或取消后释放媒体轨道。 */
    async function share() {
      setAuthorize(null);
      setRefreshing(true);
      let stream: MediaStream | undefined;
      try {
        stream = await requestCoverTab();
        for (const { item, template } of awaitingShare) await repair(item, template, stream);
      } catch (reason) {
        failures.push(reason instanceof Error ? reason.message : "封面更新失败");
      } finally {
        releaseCoverTab(stream);
        finish();
      }
    }
    /** 仅当前批次结束刷新提示，并一次展示所有单项失败。 */
    function finish() {
      if (signal.aborted) return;
      setRefreshing(false);
      if (failures.length) toast.error("部分封面更新失败", { description: failures.splice(0).join("；") });
    }
    void (async () => {
      for (const item of [...cached, ...unchecked]) await repair(item);
      finish();
      if (!signal.aborted && awaitingShare.length) setAuthorize(() => share);
    })();
    return () => controller.abort();
  }, [active, batch, address]);

  useEffect(() => {
    if (previousAddress.current !== address) {
      previousAddress.current = address;
      setTemplates([]); setLoading(true);
    }
    let mounted = true;
    // StrictMode 的临时挂载不发请求，只有首次或切换服务时显示首次加载占位。
    queueMicrotask(() => { if (mounted) void load(); });
    return () => { mounted = false; running.current?.abort(); };
  }, [revision, load]);
  return { templates, loading, error, refreshing, authorize, refresh: (): void => { void load(); }, sync: () => { void load(true); } };
}

type Collection = Pick<ReturnType<typeof useTemplateCollection>, "templates" | "loading" | "error" | "refresh">;

/** 真实读取状态映射为视图状态。 */
function sectionState(collection: Collection): SectionState {
  return collection.loading ? "loading" : collection.error ? "error" : "ready";
}

/** 最近五项与分页网格共用卡片操作；删除成功通知编辑区并重新读取列表。 */
export function TemplateCollection({ collection, onCreate, onSelect, onDelete, editing, query = "" }: Props & {
  collection: Collection;
  onCreate: () => void;
  editing?: EditingState | null;
  query?: string;
}) {
  const [deleting, setDeleting] = useState<TemplateSummary | null>(null);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [query]);
  const sorted = [...collection.templates].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const filtered = sorted.filter((template) => template.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 15));
  const currentPage = Math.min(page, pages);
  const actions = {
    onOpen: (template: TemplateSummary) => onSelect({ templateId: template.template_id }),
    onDelete: setDeleting,
    onCopyId: async (template: TemplateSummary) => { try { await navigator.clipboard.writeText(template.template_id); toast.success("模板 ID 已复制"); } catch { toast.error("复制失败，请稍后重试"); } },
  };
  return <Tooltip.Provider delayDuration={350}>
    {!query.trim() && <section aria-label="最近修改的模板">
      {sectionState(collection) === "ready" && sorted.length > 0 && <h3 className="mb-2 text-sm text-muted-foreground">最近修改</h3>}
      <TemplateSection state={sectionState(collection)} templates={sorted.slice(0, 5)} error={collection.error} onRefresh={collection.refresh} onCreate={onCreate}
        editing={editing ? { id: editing.id, dirty: editing.dirty } : null} {...actions} />
    </section>}
    {query.trim() && collection.loading && <p role="status">正在读取模板…</p>}
    {query.trim() && collection.error && <p role="alert">{collection.error}<Button variant="ghost" onClick={collection.refresh}>重试</Button></p>}
    {sectionState(collection) === "ready" && (sorted.length > 0 || query.trim()) && <section aria-label="全部模板" className="mt-10 space-y-5 border-t border-line pt-6">
      <div className="flex items-center justify-between gap-3"><h3 className="sketch-text text-xl">{query.trim() ? "搜索结果" : "全部模板"}</h3><span role="status" className="text-xs text-muted-foreground">共 {filtered.length} 个模板 · 第 {currentPage} / {pages} 页</span></div>
      {filtered.length === 0 ? <p className="py-12 text-center text-sm text-muted-foreground">没有找到匹配的模板，请换个名称试试。</p> : <div className="grid grid-cols-2 gap-x-5 gap-y-8 sm:grid-cols-3 xl:grid-cols-5">
        {filtered.slice((currentPage - 1) * 15, currentPage * 15).map((template) => <div key={template.template_id} className="min-w-0">
          <TemplateCard template={template} editing={template.template_id === editing?.id} dirty={editing?.dirty ?? false}
            onOpen={() => actions.onOpen(template)} onDelete={() => actions.onDelete(template)} onCopyId={() => actions.onCopyId(template)} />
          <h4 title={template.name} className="mt-3 truncate text-sm">{template.name}</h4>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{template.description || "暂无模板说明"}</p>
          <p className="mt-1 text-[11px] text-fg-subtle">{template.updated_at.slice(0, 10)} · {template.track_count ?? 0} 个对象</p>
        </div>)}
      </div>}
      {pages > 1 && <nav aria-label="模板分页" className="flex items-center justify-center gap-4">
        <Button variant="outline" size="sm" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft aria-hidden="true" />上一页</Button>
        <span className="text-sm">{currentPage} / {pages}</span>
        <Button variant="outline" size="sm" disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}>下一页<ChevronRight aria-hidden="true" /></Button>
      </nav>}
    </section>}
    <DeleteTemplateDialog target={deleting} onClose={() => setDeleting(null)}
      onDelete={async (template) => { await deleteTemplate(template.template_id); onDelete?.({ templateId: template.template_id }); collection.refresh(); }} />
  </Tooltip.Provider>;
}

/** 新建入口、服务状态与模板网格共用同一次列表读取。 */
export function TemplateHome({ onSelect, onDelete, onOpenRemotion, onOpenSettings = () => {}, editing, revision = 0, active = true }: Props & {
  onOpenRemotion: () => void;
  onOpenSettings?: () => void;
  editing?: EditingState | null;
  revision?: number;
  active?: boolean;
}) {
  const cloud = useTemplateCollection(revision, active);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const state = sectionState(cloud);
  return <>
    <HomeView cloudState={state} onCreate={() => setCreating(true)} onOpenEffects={onOpenRemotion} onOpenSettings={onOpenSettings}
      count={state === "ready" ? cloud.templates.length : 0} refreshing={cloud.loading || cloud.refreshing} onRefresh={cloud.sync} query={query} onSearch={setQuery}>
      {cloud.authorize && <Button type="button" variant="outline" size="sm" onClick={cloud.authorize}>共享当前标签页以补截封面</Button>}
      <TemplateCollection collection={cloud} query={query} onCreate={() => setCreating(true)} onSelect={onSelect} onDelete={onDelete} editing={editing} />
    </HomeView>
    <NewTemplateDialog open={creating} existingNames={cloud.templates.map((template) => template.name)} onClose={() => setCreating(false)} onCreate={({ name, description }) => { onSelect({ templateId: null, name, description }); setCreating(false); }} />
  </>;
}
