/** 模板编辑工作区：接收主页选择，展示模板信息，协调效果编辑、保存和未保存切换保护。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { recovery } from "@/features/updates/recovery";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, CircleCheck, Pencil, SquarePen } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import * as api from "./api";
import { readCatalog } from "./sdk";
import { activeDraft, boundText, newDraft, toDraft, type Draft, type EffectAsset, type Template, type TextRole, type PreviewMedia } from "./model";
import { AppliedEffects, EffectAssets } from "./EffectAssets";
import { isTextTarget } from "./effects";
import { addTrack, previewContext, resolveTemplate, removeTrack, setBilingual, setTrackTiming, trackDraft, updateTrack } from "./tracks";
import { defaultPreviewUrls, readPreviewVideos } from "./media";
import { compactTransitions, previewTransitions, setTransitionDuration, transitionItems } from "./transitions";
import { TemplateInspector, TransitionInspector, type InspectorTab } from "./TemplateInspector";
import { TemplatePreview } from "./TemplatePreview";
import { SpriteCatalog, SpriteClips } from "./SpritePanels";
import { SpriteClipInspector } from "./SpriteClipInspector";
import { addClip, bindClip, clipSlot, hiddenTextClips, moveClip, removeClip, setClipParameter, setClipTransform, timeClips } from "./clips";
import { exampleKeyword } from "./timeline";
import { transformFields } from "@/features/sprites/parameters";
import { useSpriteCatalog } from "./useSpriteCatalog";
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
  return <ResizablePanelGroup orientation="horizontal" disabled={!desktop} data-layout={desktop ? "desktop" : "narrow"} className="template-workspace-columns" style={{ height: desktop ? "100%" : "auto", minHeight: desktop ? 0 : undefined }}>
    <ResizablePanel id="assets" data-workspace-panel="assets" defaultSize={284} minSize={240} maxSize="28%" className="min-w-0">{assets}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整资产与画布宽度" className="template-workspace-handle" />
    <ResizablePanel id="canvas" data-workspace-panel="canvas" minSize={420} className="min-w-0">{canvas}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整画布与参数宽度" className="template-workspace-handle" />
    <ResizablePanel id="inspector" data-workspace-panel="inspector" defaultSize={300} minSize={260} maxSize="32%" className="min-w-0">{inspector}</ResizablePanel>
  </ResizablePanelGroup>;
}

/** 导航与主页共享当前编辑状态，不包含可变草稿。 */
export interface EditingState { id: string | null; name: string; environment: api.Environment; dirty: boolean }

/** 只有成功读取或明确放弃时才替换草稿；保存始终使用当前模板的环境和 ID。 */
export function TemplateWorkspace({ selection = null, deletedTemplate = null, onHome, onOpenRemotion, onEditingState, active = true }: {
  active?: boolean;
  onEditingState?: (state: EditingState | null) => void;
  selection?: TemplateSelection | null;
  deletedTemplate?: TemplateSelection | null;
  onHome: () => void;
  /** 回到字效工作区修改资产本身（颜色、动画等不在模板里调整的参数）。 */
  onOpenRemotion?: () => void;
}) {
  const [environment, setEnvironment] = useState<api.Environment>("cloud");
  const [current, setCurrent] = useState<Template | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<{ field: "name" | "description"; value: string } | null>(null);
  const [media, setMedia] = useState<PreviewMedia>();
  const [mediaError, setMediaError] = useState("");
  const [mediaAttempt, setMediaAttempt] = useState(0);
  const [baseline, setBaseline] = useState("");
  const [catalog, setCatalog] = useState<EffectAsset[]>(() => readCatalog());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [assetError, setAssetError] = useState("");
  const [action, setAction] = useState<TemplateSelection | null>(null);
  const [openRequest, setOpenRequest] = useState<TemplateSelection | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [assetSource, setAssetSource] = useState("effects");
  const [target, setTarget] = useState<string | null>("title");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("timing");
  const [textTarget, setTextTarget] = useState<TextRole>("title");
  const lastTextTrack = useRef<string | null>("title");
  const lock = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const mounted = useRef(false);
  const handledSelection = useRef<TemplateSelection | null>(null);
  const spriteCatalog = useSpriteCatalog(environment, (draft?.clips.length ?? 0) > 0);
  const [transitionRandom, setTransitionRandom] = useState<readonly [number, number]>(() => [Math.random(), Math.random()]);
  const visibleDraft = useMemo(() => draft ? activeDraft(draft) : null, [draft]);
  const previewSelection = useMemo(() => visibleDraft ? previewTransitions(visibleDraft, transitionRandom, spriteCatalog.assets ?? []) : null, [visibleDraft, transitionRandom, spriteCatalog.assets]);
  const previewDraft = previewSelection?.draft;
  const preview = useMemo(() => {
    if (!previewDraft || !media) return { timeline: undefined, error: "" };
    try { return { timeline: resolveTemplate(previewDraft, previewContext(media)), error: "" }; }
    catch (reason) { return { timeline: undefined, error: reason instanceof Error ? reason.message : "时间规则无法应用到当前视频" }; }
  }, [previewDraft, media]);
  const previewLength = preview.timeline?.duration ?? 0;
  // 占用文字或交界处的片段按本次预览计算展示区间，不改变草稿。
  const timedDraft = useMemo(() => draft && preview.timeline ? timeClips(draft, preview.timeline) : draft, [draft, preview.timeline]);
  const timedClips = timedDraft ? activeDraft(timedDraft).clips : undefined;
  const previewClips = useMemo(() => previewDraft && preview.timeline ? timeClips(previewDraft, preview.timeline).clips : [], [previewDraft, preview.timeline]);
  // 所作用文字在当前预览中不显示时，字效也不显示，避免按原有时间出现在画面上。
  const hiddenClips = useMemo(() => previewDraft && preview.timeline ? hiddenTextClips(previewDraft, preview.timeline) : new Set<string>(), [previewDraft, preview.timeline]);
  const draftTracks = draft?.tracks;
  const spriteClips = useMemo(() => previewClips.map((clip) => {
    const asset = spriteCatalog.assets?.find((item) => item.id === clip.sprite_id);
    // 作用于文字的字效显示该文字对象的示例文字与关键词，合成时由总线换成实际标题或切片。
    const text = boundText(draftTracks ?? [], clip);
    return {
      id: clip.id,
      spriteId: clip.sprite_id,
      aspect: asset?.aspect ?? 9 / 16,
      name: asset?.name ?? "Remotion 资产",
      start: clip.start,
      end: Math.min(previewLength, clip.start + (clip.duration ?? asset?.seconds ?? 0)),
      length: clip.duration ?? asset?.seconds ?? 0,
      slot: clipSlot(clip, asset),
      textTrackId: text?.id,
      boundary: clip.transition_boundary,
      values: text && clip.text_target ? { ...clip.parameters, text: text.editor[clip.text_target], keywords: [exampleKeyword(text.editor, clip.text_target, text.language)].filter(Boolean) } : clip.parameters,
      transform: clip.transform,
      canvas: asset ? { width: asset.width, height: asset.height, box: asset.box, movable: transformFields(asset.kind).includes("x") } : undefined,
    };
  }).filter((clip) => clip.end > clip.start && !hiddenClips.has(clip.id)), [previewClips, hiddenClips, draftTracks, spriteCatalog.assets, previewLength]);
  const dirty = draft !== null && (current === null || JSON.stringify(draft) !== baseline || (editing !== null && editing.value !== draft[editing.field]));
  useUpdateBlocker("模板尚未保存或正在处理", dirty || busy || loading || !!openRequest);
  useEffect(() => { recovery.template = current ? { environment, templateId: current.template_id } : null; }, [current, environment]);
  useEffect(() => { onEditingState?.(draft ? { id: current?.template_id ?? null, name: draft.name, environment, dirty } : null); }, [draft?.name, !!draft, current?.template_id, environment, dirty, onEditingState]);
  useEffect(() => {
    function saveShortcut(event: KeyboardEvent) {
      if (active && !event.defaultPrevented && !document.querySelector('[role="dialog"]') && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); form.current?.requestSubmit(); }
    }
    window.addEventListener("keydown", saveShortcut);
    return () => window.removeEventListener("keydown", saveShortcut);
  }, [active]);

  const selectedTrack = draft?.tracks.find((track) => track.id === target);
  const assetTrack = selectedTrack ?? draft?.tracks.find((track) => track.id === lastTextTrack.current);
  const selectedDraft = draft && selectedTrack ? trackDraft(draft, selectedTrack) : null;
  // IMS 对象与 Remotion 片段共用一个选中状态，右栏只显示其中一种设置。
  // 文字被 Remotion 字效接管后，选中该文字对象就是选中字效：右栏、时间轴与预览都以字效为准，IMS 面板不再混用。
  const effectiveTarget = draft?.clips.find((clip) => boundText(draft.tracks, clip)?.id === target)?.id ?? target;
  const selectedClip = draft?.clips.find((clip) => clip.id === effectiveTarget && clip.transition_boundary !== undefined) ?? timedClips?.find((clip) => clip.id === effectiveTarget);
  const clipText = selectedClip && draft ? boundText(draft.tracks, selectedClip) : undefined;
  const selectedText = clipText ?? selectedTrack;
  const bilingualTarget = selectedText?.target === "title" || selectedText?.target === "subtitle" ? selectedText.target : undefined;
  const english = draft?.tracks.find((track) => track.target === bilingualTarget && track.language === "en");

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // 主页删除模板后取消对应读取、清空对应编辑区，删除其他模板不影响当前草稿。
  useEffect(() => {
    if (!deletedTemplate) return;
    if (openRequest?.templateId === deletedTemplate.templateId && openRequest.environment === deletedTemplate.environment) {
      setOpenRequest(null); setLoading(false); setError("");
    }
    if (!current || deletedTemplate.templateId !== current.template_id || deletedTemplate.environment !== environment) return;
    setCurrent(null); setDraft(null); setEditing(null); setBaseline(""); setError("");
  }, [deletedTemplate, current, environment, openRequest]);

  // 默认地址（本机保存的本地视频优先）与弹窗均读取真实元数据；已选择来源时不再启动默认读取。
  useEffect(() => {
    if (!draft || media) return;
    const controller = new AbortController();
    setMediaError("");
    void defaultPreviewUrls().then((urls) => readPreviewVideos(urls, controller.signal)).then((sources) => {
      if (!controller.signal.aborted) setMedia(sources);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setMediaError(reason instanceof Error ? reason.message : "预览视频读取失败");
    });
    return () => controller.abort();
  }, [!!draft, draft?.name, current?.template_id, environment, media, mediaAttempt]);

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
        setEditing(null);
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
    const clip = draft?.clips.find((item) => item.id === next);
    const selected = draft?.tracks.find((track) => track.id === next) ?? (draft && clip ? boundText(draft.tracks, clip) : undefined);
    if (selected && isTextTarget(selected.target)) { setTextTarget(selected.target); lastTextTrack.current = selected.id; }
  }

  /** 添加资产失败时使用提示框；参数错误显示在工作区，失败时保留当前草稿。 */
  function editTrack(change: () => Draft, addingAsset = false) {
    try { setDraft(change()); setError(""); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "轨道修改失败";
      if (addingAsset) setAssetError(message);
      else setError(message);
    }
  }

  /** 成功保存响应建立新基线；失败保留原草稿和待切换目标，不自动重发。 */
  async function persist(nextSelection?: TemplateSelection, savingDraft = draft) {
    if (!savingDraft || lock.current || loading) return;
    if (editing?.field === "name" && !editing.value.trim()) { setError("请输入模板名称"); return; }
    // 时间输入保留局部编辑值，写入前检查；模板配置继续由 saveTemplate 校验。
    const timingInputs = form.current?.querySelectorAll<HTMLInputElement>('[aria-label="轨道时间设置"] input');
    if (timingInputs && [...timingInputs].some((input) => !input.checkValidity())) { setInspectorTab("timing"); return; }
    const transitionInput = form.current?.querySelector<HTMLInputElement>('[aria-label="转场时间设置"] input[type="number"]');
    if (transitionInput && !transitionInput.checkValidity()) { transitionInput.reportValidity(); return; }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      // Remotion 字效保存文字区间，转场保留自身时长，与 IMS 对象在同一次请求里保存。
      const saveDraft = preview.timeline ? timeClips(savingDraft, preview.timeline, "save") : savingDraft;
      const saved = await api.saveTemplate(setTransitionDuration(saveDraft, savingDraft.transition_duration_seconds), current?.template_id, environment, current?.library);
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

  /** 名称或描述失焦时复用完整保存流程；无变化不写入，失败仍保留新值供重试。 */
  function saveMetadata() {
    if (!draft || !editing) return;
    const value = editing.value.trim();
    if (editing.field === "name" && !value) { setError("请输入模板名称"); return; }
    setEditing(null);
    if (value === draft[editing.field]) return;
    const next = { ...draft, [editing.field]: value };
    setDraft(next);
    void persist(undefined, next);
  }

  return (
    <div className={cn("@container min-w-0", draft ? "space-y-3" : "flex min-h-full flex-col gap-3")}>
      {error && !action && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
      {openRequest && error && !action && <Button type="button" variant="outline" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>重试打开模板</Button>}
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner />正在读取模板…</p>}
      {!draft ? (
        <section aria-label="模板编辑入口" className="flex flex-1">
          <Empty>
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
        <form ref={form} noValidate className="template-workspace overflow-hidden bg-card" onSubmit={(event) => { event.preventDefault(); void persist(); }}>
          <fieldset disabled={busy || loading} className="min-w-0 disabled:opacity-60">
            <section aria-label="模板信息" className="template-workspace-header flex min-h-16 flex-wrap items-center justify-between gap-4 border-b px-5 py-3 lg:px-7">
              <div className="flex min-w-0 items-center gap-4"><Button type="button" variant="ghost" size="icon-sm" aria-label="返回主页" onClick={onHome}><ArrowLeft className="size-4" /></Button>
                <dl className="min-w-0 space-y-1">
                  {(["name", "description"] as const).map((field) => {
                    const label = field === "name" ? "模板名称" : "模板描述";
                    const Control = field === "name" ? Input : Textarea;
                    return <div key={field} className="flex min-w-0 items-center gap-2">
                      <dt className="sr-only">{label}</dt>
                      <dd className="min-w-0">
                        {editing?.field === field ? <Control autoFocus aria-label={label} value={editing.value}
                          required={field === "name"} maxLength={field === "name" ? 100 : 1000} className="min-w-48" rows={2}
                          onChange={(event) => setEditing({ field, value: event.target.value })} onBlur={saveMetadata}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") { event.preventDefault(); setEditing(null); setError(""); }
                            if (event.key === "Enter" && field === "name" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.blur(); }
                          }} />
                          : <span aria-label={label} className={field === "name" ? "block truncate text-[17px] font-semibold tracking-tight" : "block truncate text-[11px] text-muted-foreground"}>{draft[field] || "暂无模板描述"}</span>}
                      </dd>
                      {editing?.field !== field && <Hint label={`编辑${label}`}><Button type="button" variant="ghost" size="icon-xs" aria-label={`编辑${label}`}
                        onClick={() => { setEditing({ field, value: draft[field] }); setError(""); }}><Pencil className="size-3" aria-hidden="true" /></Button></Hint>}
                      {field === "name" && <div><dt className="sr-only">当前环境</dt><dd aria-label="当前环境" className="template-workspace-environment rounded-full px-2 py-0.5 text-[11px]">{environment === "cloud" ? "云端" : "本地"}</dd></div>}
                    </div>;
                  })}
                </dl>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <span className="template-workspace-status flex items-center gap-2 text-[11px] text-muted-foreground"><span className={`size-1.5 rounded-full ${dirty ? "bg-warning" : "bg-success"}`} />{current ? dirty ? "有未保存的修改" : "已保存" : "新模板 · 尚未保存"}</span>
                <Button type="submit" className="template-workspace-save h-9 gap-2 rounded-lg px-4 text-xs">{busy ? <Spinner /> : <CircleCheck className="size-4" aria-hidden="true" />}{busy ? "正在保存…" : "保存模板"}</Button>
              </div>
            </section>
            <WorkspaceColumns
              assets={<aside className="template-workspace-assets h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <div className="flex h-14 items-center gap-1 border-b border-line px-3" role="tablist" aria-label="素材来源">
                  <button type="button" role="tab" aria-selected={assetSource === "effects"} className="studio-source-tab" onClick={() => setAssetSource("effects")}>特效素材</button>
                  <button type="button" role="tab" aria-selected={assetSource === "ai"} className="studio-source-tab" onClick={() => { setAssetSource("ai"); spriteCatalog.refresh(); }}>AI 资产</button>
                </div>
                <div hidden={assetSource !== "effects"}><EffectAssets editor={assetTrack?.editor} catalog={catalog} textTarget={textTarget} textTracks={visibleDraft!.tracks} selectedText={lastTextTrack.current} onSelectText={selectTarget} textEditor={draft.tracks.find((track) => track.id === lastTextTrack.current)?.editor} onTextTarget={(role) => {
                  setTextTarget(role);
                  const text = draft.tracks.find((track) => track.target === role);
                  setTarget(text?.id ?? null);
                  lastTextTrack.current = text?.id ?? null;
                }} onAsset={(asset, role) => editTrack(() => {
                  if (asset.category === "vfx/normal" && draft.clips.some((clip) => clipSlot(clip, spriteCatalog.assets?.find((item) => item.id === clip.sprite_id)) === "vfx"))
                    throw new Error("画面特效最多一个，已添加的 Remotion 视频特效也占用这个名额，请先移除");
                  const result = addTrack(draft, asset, role, lastTextTrack.current ?? undefined);
                  setTarget(result.id);
                  const added = result.draft.tracks.find((track) => track.id === result.id)!;
                  if (isTextTarget(added.target)) { setTextTarget(added.target); lastTextTrack.current = added.id; }
                  return result.draft;
                }, true)} /></div>
                <div hidden={assetSource !== "ai"}><SpriteCatalog environment={environment} catalog={spriteCatalog} placed={new Set(draft.clips.map((clip) => clip.sprite_id))} onAdd={(asset) => editTrack(() => addClip(draft, asset, spriteCatalog.assets ?? [], textTarget, draft.tracks.find((track) => track.id === lastTextTrack.current)?.language), true)} /></div>
              </aside>}
              canvas={<main className="template-workspace-canvas h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <TemplatePreview draft={previewDraft!} media={media} resolved={preview.timeline} mediaError={mediaError || preview.error} onRetryMedia={() => setMediaAttempt((value) => value + 1)} onMediaChange={(next) => { setMedia(next); setMediaError(""); }} videoInputKey={`${environment}:${current?.template_id ?? draft.name}`} onCatalog={setCatalog} selectedId={effectiveTarget} selectionTargets={previewSelection?.targets} onSelect={(id) => { selectTarget(previewSelection?.targets.get(id) ?? id); }} sprites={spriteClips} onSpriteMove={(id, x, y) => editTrack(() => setClipTransform(draft, previewSelection?.targets.get(id) ?? id, { x, y }))}
                />
              </main>}
              inspector={<aside className="template-workspace-inspector relative h-full min-w-0 overflow-y-auto xl:border-l"><AppliedEffects draft={visibleDraft!} catalog={catalog} resolved={preview.timeline} selected={target} onSelect={selectTarget} />
                <SpriteClips environment={environment} clips={timedClips ?? []} catalog={spriteCatalog} duration={preview.timeline?.duration} hidden={hiddenClips} selected={selectedClip?.id ?? null} onSelect={selectTarget} onRemove={(id) => editTrack(() => removeClip(draft, id))} />
                {bilingualTarget && <div className="space-y-2 border-b px-4 py-3">
                  <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={!!english && english.enabled !== false} onChange={(event) => {
                    const enabled = event.target.checked;
                    editTrack(() => {
                      const next = setBilingual(draft, bilingualTarget, enabled);
                      if (!enabled && selectedText?.language === "en") {
                        const chinese = next.tracks.find((track) => track.target === bilingualTarget && track.language !== "en")!;
                        setTarget(chinese.id); lastTextTrack.current = chinese.id;
                      }
                      return next;
                    });
                  }} />中英双语</label>
                  {english?.enabled !== false && english && <Button type="button" variant="outline" size="sm" onClick={() => { selectTarget(english.id); setInspectorTab("appearance"); }}>编辑英文{bilingualTarget === "title" ? "标题" : "字幕"}</Button>}
                  {selectedText?.language === "en" && <p className="text-[11px] text-muted-foreground">时间跟随中文{bilingualTarget === "title" ? "标题" : "字幕"}，合成时自动翻译实际文案。</p>}
                </div>}
                {selectedClip && <SpriteClipInspector key={selectedClip.id} clip={selectedClip} asset={spriteCatalog.assets?.find((item) => item.id === selectedClip.sprite_id)} duration={preview.timeline?.duration}
                  textTrack={clipText} textTracks={visibleDraft!.tracks} textResolved={clipText && preview.timeline?.tracks[clipText.id]} onOpenRemotion={onOpenRemotion}
                  onSample={(value) => clipText && editTrack(() => {
                    const role = clipText.target as "title" | "subtitle";
                    const edit = trackDraft(draft, clipText);
                    return updateTrack(draft, clipText.id, { ...edit, editor: { ...edit.editor, [role]: value, ...(role === "title" ? { titleKeyword: "" } : {}) } });
                  })}
                  onTextTiming={(timing) => clipText && editTrack(() => setTrackTiming(draft, clipText.id, timing))}
                  onParameter={(key, value) => editTrack(() => setClipParameter(draft, selectedClip.id, key, value))}
                  onTransform={(patch) => editTrack(() => setClipTransform(draft, selectedClip.id, patch))}
                  onStart={(start) => editTrack(() => moveClip(draft, selectedClip.id, start))}
                  onBind={(binding) => editTrack(() => {
                    const next = bindClip(draft, selectedClip.id, binding);
                    const text = boundText(next.tracks, next.clips.find((clip) => clip.id === selectedClip.id)!);
                    if (text && isTextTarget(text.target)) { setTextTarget(text.target); lastTextTrack.current = text.id; }
                    return next;
                  })}
                  onRemove={() => { editTrack(() => removeClip(draft, selectedClip.id)); setTarget(null); }}
                  onClose={() => setTarget(null)} />}
                {(target === "transitions" || selectedTrack?.target === "transition" || selectedClip?.transition_boundary !== undefined) && <TransitionInspector draft={draft} catalog={catalog} sprites={spriteCatalog.assets ?? []}
                  onChoose={(id, value) => editTrack(() => {
                    const existing = transitionItems(draft).find((item) => item.id === id);
                    let next = { ...draft, tracks: draft.tracks.filter((track) => track.id !== id), clips: draft.clips.filter((clip) => clip.id !== id) };
                    if (value.startsWith("sprite:")) {
                      const asset = spriteCatalog.assets?.find((item) => item.id === value.slice(7));
                      if (!asset) throw new Error("转场资产不存在");
                      next = addClip(next, asset, spriteCatalog.assets ?? []);
                      if (existing) next = { ...next, clips: next.clips.map((clip, index) => index === next.clips.length - 1 ? { ...clip, id: existing.id, transition_boundary: existing.transition_boundary } : clip) };
                    } else {
                      const asset = catalog.find((item) => item.id === value);
                      if (!asset) throw new Error("转场效果不存在");
                      const added = addTrack(next, asset, "transition", undefined, existing?.transition_boundary);
                      next = existing ? { ...added.draft, tracks: added.draft.tracks.map((track) => track.id === added.id ? { ...track, id: existing.id } : track) } : added.draft;
                    }
                    setTarget("transitions");
                    return next;
                  })}
                  onRemove={(id) => editTrack(() => {
                    setTarget("transitions");
                    return compactTransitions({ ...draft, tracks: draft.tracks.filter((track) => track.id !== id), clips: draft.clips.filter((clip) => clip.id !== id) });
                  })}
                  onDuration={(seconds) => editTrack(() => setTransitionDuration(draft, seconds))}
                  onMode={(mode) => { setTransitionRandom([Math.random(), Math.random()]); editTrack(() => ({ ...draft, transition_mode: mode })); }}
                  onEnabled={(enabled) => editTrack(() => ({ ...draft, transition_enabled: enabled }))}
                  onSelect={selectTarget} />}
                {selectedTrack && !selectedClip && selectedTrack.target !== "transition" && <TemplateInspector track={selectedTrack} draft={selectedDraft!} catalog={catalog} duration={previewLength} resolved={preview.timeline?.tracks[selectedTrack.id]} tab={inspectorTab} onTabChange={setInspectorTab} onTimingChange={(timing) => editTrack(() => setTrackTiming(draft, selectedTrack.id, timing))} onEffectChange={(next) => editTrack(() => updateTrack(draft, selectedTrack.id, next))} onRemove={() => editTrack(() => removeTrack(draft, selectedTrack.id))} onClose={() => setTarget(null)} />}
              </aside>}
            />
          </fieldset>
        </form>
      )}
      <Dialog open={!!assetError} onOpenChange={(open) => { if (!open) setAssetError(""); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>无法添加特效</DialogTitle><DialogDescription>{assetError}</DialogDescription></DialogHeader>
          <DialogFooter><Button type="button" onClick={() => setAssetError("")}>知道了</Button></DialogFooter>
        </DialogContent>
      </Dialog>
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
