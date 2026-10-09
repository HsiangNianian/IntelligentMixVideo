/** 模板编辑工作区：接收主页选择，展示模板信息，协调效果编辑、保存和未保存切换保护。 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { CircleCheck, SquarePen } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import * as api from "./api";
import { readCatalog } from "./sdk";
import { newDraft, toDraft, type Draft, type EffectAsset, type Template, type TextRole, type MasterVideo } from "./model";
import { AppliedEffects, EffectAssets } from "./EffectAssets";
import { isTextTarget } from "./effects";
import { addTrack, previewDuration, removeTrack, setTrackRange, setTrackTiming, trackDraft, updateTrack } from "./tracks";
import { TemplateInspector, type InspectorTab } from "./TemplateInspector";
import { TemplatePreview } from "./TemplatePreview";
import type { TemplateSelection } from "./TemplateHome";
import "./template-workspace.css";
import { Spinner } from "@/components/ui/spinner";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/** 窗口宽度仅改变排列和调整权限，保留各栏组件、输入草稿与播放器实例。 */
function WorkspaceColumns({ assets, canvas, inspector }: { assets: ReactNode; canvas: ReactNode; inspector: ReactNode }) {
  const [desktop, setDesktop] = useState(() => window.innerWidth >= 1280);
  useEffect(() => {
    const update = () => setDesktop(window.innerWidth >= 1280);
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return <ResizablePanelGroup orientation="horizontal" disabled={!desktop} data-layout={desktop ? "desktop" : "narrow"} className="template-workspace-columns" style={{ height: desktop ? "calc(100dvh - 92px)" : "auto", minHeight: desktop ? 760 : undefined }}>
    <ResizablePanel id="assets" data-workspace-panel="assets" defaultSize={205} minSize={205} maxSize="28%" className="min-w-0">{assets}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整资产与画布宽度" className="template-workspace-handle" />
    <ResizablePanel id="canvas" data-workspace-panel="canvas" minSize={420} className="min-w-0">{canvas}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整画布与参数宽度" className="template-workspace-handle" />
    <ResizablePanel id="inspector" data-workspace-panel="inspector" defaultSize={250} minSize={250} maxSize="32%" className="min-w-0">{inspector}</ResizablePanel>
  </ResizablePanelGroup>;
}

/** 只有成功读取或明确放弃时才替换草稿；保存始终使用当前模板的环境和 ID。 */
export function TemplateWorkspace({ selection = null, onHome }: {
  selection?: TemplateSelection | null;
  onHome: () => void;
}) {
  const [environment, setEnvironment] = useState<api.Environment>("cloud");
  const [current, setCurrent] = useState<Template | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [media, setMedia] = useState<MasterVideo>();
  const [baseline, setBaseline] = useState("");
  const [catalog, setCatalog] = useState<EffectAsset[]>(() => readCatalog());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [action, setAction] = useState<TemplateSelection | null>(null);
  const [openRequest, setOpenRequest] = useState<TemplateSelection | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [target, setTarget] = useState<string | null>("title");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("timing");
  const [textTarget, setTextTarget] = useState<TextRole>("title");
  const lastTextTrack = useRef<string | null>("title");
  const lock = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const mounted = useRef(false);
  const handledSelection = useRef<TemplateSelection | null>(null);
  const dirty = draft !== null && (current === null || JSON.stringify(draft) !== baseline);
  const selectedTrack = draft?.tracks.find((track) => track.id === target);
  const assetTrack = selectedTrack ?? draft?.tracks.find((track) => track.id === lastTextTrack.current);
  const selectedDraft = draft && selectedTrack ? trackDraft(draft, selectedTrack) : null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // 主页的新选择逐次消费，保存或读取期间等待当前操作结束。
  useEffect(() => {
    if (!selection || handledSelection.current === selection || loading || busy) return;
    handledSelection.current = selection;
    setOpenRequest(null);
    setError("");
    if (selection.templateId && selection.templateId === current?.template_id && selection.environment === environment) return;
    if (dirty) setAction(selection);
    else setOpenRequest(selection);
  }, [selection, loading, busy, current, environment, dirty]);

  // 每次读取拥有独立取消信号；迟到响应、卸载及 StrictMode 重建都不能替换当前草稿。
  useEffect(() => {
    if (!openRequest) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const read = async () => {
      try {
        const template = openRequest.templateId !== null
          ? await api.getTemplate(openRequest.templateId, openRequest.environment, controller.signal)
          : null;
        if (controller.signal.aborted) return;
        const next = template ? toDraft(template) : {
          ...newDraft(),
          name: openRequest.templateId === null ? openRequest.name : "",
          description: openRequest.templateId === null ? openRequest.description : "",
        };
        setCurrent(template);
        setDraft(next);
        setMedia(undefined);
        setBaseline(JSON.stringify(next));
        setEnvironment(openRequest.environment);
        setTarget(next.tracks[0]?.id ?? null);
        const initialText = next.tracks.find((track) => isTextTarget(track.target));
        lastTextTrack.current = initialText?.id ?? null;
        setTextTarget(initialText && isTextTarget(initialText.target) ? initialText.target : "title");
        setAction(null);
        setOpenRequest(null);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "模板读取失败，请重试");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void read();
    return () => controller.abort();
  }, [openRequest, attempt]);

  /** 资产和已添加对象共用选中状态，文字资产沿用最近选择的文字对象。 */
  function selectTarget(next: string) {
    setTarget(next);
    const selected = draft?.tracks.find((track) => track.id === next);
    if (selected && isTextTarget(selected.target)) { setTextTarget(selected.target); lastTextTrack.current = selected.id; }
  }

  /** 所有实例修改共用错误展示，非法输入保留当前可用草稿。 */
  function editTrack(change: () => Draft) {
    try { setDraft(change()); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "轨道修改失败"); }
  }

  /** 成功保存响应建立新基线；失败保留原草稿和待切换目标，不自动重发。 */
  async function persist(nextSelection?: TemplateSelection) {
    if (!draft || lock.current || loading) return;
    // 时间输入保留局部编辑值，写入前检查；模板配置继续由 saveTemplate 校验。
    const timingInputs = form.current?.querySelectorAll<HTMLInputElement>('[aria-label="轨道时间设置"] input');
    if (timingInputs && [...timingInputs].some((input) => !input.checkValidity())) { setInspectorTab("timing"); return; }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const saved = await api.saveTemplate(draft, current?.template_id, environment);
      if (!mounted.current) return;
      const next = toDraft(saved);
      setCurrent(saved);
      setDraft(next);
      setBaseline(JSON.stringify(next));
      toast.success(`模板「${saved.name}」已保存`);
      if (nextSelection) { setOpenRequest(nextSelection); setAttempt((value) => value + 1); }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "保存失败，请重试");
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <div className="@container min-w-0 space-y-3">
      {error && !action && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
      {openRequest && error && !action && <Button type="button" variant="outline" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>重试打开模板</Button>}
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner />正在读取模板…</p>}
      {!draft ? (
        <section aria-label="模板编辑入口">
          <Empty className="min-h-[60dvh] border bg-card">
            <EmptyHeader>
              <EmptyMedia variant="icon"><SquarePen aria-hidden="true" /></EmptyMedia>
              <EmptyTitle>还没有打开模板</EmptyTitle>
              <EmptyDescription>请从主页选择已有模板或创建新模板。</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button type="button" variant="outline" onClick={onHome}>前往主页</Button>
            </EmptyContent>
          </Empty>
        </section>
      ) : (
        <form ref={form} noValidate className="template-workspace overflow-hidden rounded-xl border bg-card" onSubmit={(event) => { event.preventDefault(); void persist(); }}>
          <fieldset disabled={busy || loading} className="min-w-0 disabled:opacity-60">
            <section aria-label="模板信息" className="template-workspace-header flex min-h-16 flex-wrap items-center justify-between gap-4 border-b px-5 py-3 lg:px-7">
              <div className="flex min-w-0 items-center gap-4"><span className="template-workspace-mark flex size-9 shrink-0 items-center justify-center rounded-lg text-xs font-black tracking-[-.06em]" aria-hidden="true">IM</span>
                <dl className="min-w-0 space-y-1"><div className="flex min-w-0 flex-wrap items-center gap-2"><div className="min-w-0"><dt className="sr-only">模板名称</dt><dd aria-label="模板名称" className="truncate text-[17px] font-semibold tracking-tight">{draft.name}</dd></div><div><dt className="sr-only">当前环境</dt><dd aria-label="当前环境" className="template-workspace-environment rounded-full px-2 py-0.5 text-[11px]">{environment === "cloud" ? "云端" : "本地"}</dd></div></div><div><dt className="sr-only">模板描述</dt><dd aria-label="模板描述" className="truncate text-[11px] text-muted-foreground">{draft.description || "暂无模板描述"}</dd></div></dl>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <span className="template-workspace-status flex items-center gap-2 text-[11px] text-muted-foreground"><span className={`size-1.5 rounded-full ${dirty ? "bg-warning" : "bg-success"}`} />{current ? dirty ? "有未保存的修改" : "已保存" : "新模板 · 尚未保存"}</span>
                <Button type="submit" className="template-workspace-save h-9 gap-2 rounded-lg px-4 text-xs">{busy ? <Spinner /> : <CircleCheck className="size-4" aria-hidden="true" />}{busy ? "正在保存…" : "保存模板"}</Button>
              </div>
            </section>
            <WorkspaceColumns
              assets={<aside className="template-workspace-assets h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <EffectAssets editor={assetTrack?.editor} catalog={catalog} textTarget={textTarget} textEditor={draft.tracks.find((track) => track.id === lastTextTrack.current)?.editor} onTextTarget={(role) => {
                  setTextTarget(role);
                  const text = draft.tracks.find((track) => track.target === role);
                  setTarget(text?.id ?? null);
                  lastTextTrack.current = text?.id ?? null;
                }} onAsset={(asset, role) => editTrack(() => {
                  const result = addTrack(draft, asset, role, lastTextTrack.current ?? undefined);
                  setTarget(result.id);
                  const added = result.draft.tracks.find((track) => track.id === result.id)!;
                  if (isTextTarget(added.target)) { setTextTarget(added.target); lastTextTrack.current = added.id; }
                  return result.draft;
                })} />
              </aside>}
              canvas={<main className="template-workspace-canvas h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <TemplatePreview draft={draft} media={media} onMediaChange={setMedia} videoInputKey={`${environment}:${current?.template_id ?? draft.name}`} onCatalog={setCatalog} selectedId={target} onSelect={selectTarget} onRangeChange={(id, start, end) => editTrack(() => setTrackRange(draft, id, start, end, media))} />
              </main>}
              inspector={<aside className="template-workspace-inspector h-full min-w-0 overflow-y-auto xl:border-l"><AppliedEffects draft={draft} catalog={catalog} duration={previewDuration(draft, media)} selected={target} onSelect={selectTarget} />
                {selectedTrack && <TemplateInspector track={selectedTrack} draft={selectedDraft!} catalog={catalog} duration={previewDuration(draft, media)} tab={inspectorTab} onTabChange={setInspectorTab} onTimingChange={(timing) => editTrack(() => setTrackTiming(draft, selectedTrack.id, timing))} onEffectChange={(next) => editTrack(() => updateTrack(draft, selectedTrack.id, next))} onRemove={() => editTrack(() => removeTrack(draft, selectedTrack.id))} onClose={() => setTarget(null)} />}
              </aside>}
            />
          </fieldset>
        </form>
      )}
      <Dialog open={!!action} onOpenChange={(open) => { if (!open && !busy && !loading) { setAction(null); setOpenRequest(null); setError(""); } }}>
        <DialogContent showCloseButton={!busy && !loading}>
          <DialogHeader><DialogTitle>保存当前修改？</DialogTitle><DialogDescription>切换前可以保存当前配置、放弃修改，或取消切换。</DialogDescription></DialogHeader>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy || loading} onClick={() => { setAction(null); setOpenRequest(null); setError(""); }}>取消</Button>
            <Button type="button" variant="outline" disabled={busy || loading} onClick={() => { setOpenRequest(action); setAttempt((value) => value + 1); }}>放弃修改</Button>
            <Button type="button" disabled={busy || loading} onClick={() => { if (action) void persist(action); }}>保存并切换</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
