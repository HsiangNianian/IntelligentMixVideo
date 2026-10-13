/** 模板编辑工作区：接收主页选择，展示模板信息，协调效果编辑、保存和未保存切换保护。 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CircleCheck, SquarePen } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import * as api from "./api";
import { readCatalog } from "./sdk";
import { newDraft, toDraft, type Draft, type EffectAsset, type Template, type TextRole, type PreviewMedia } from "./model";
import { AppliedEffects, EffectAssets } from "./EffectAssets";
import { isTextTarget } from "./effects";
import { addTrack, previewContext, resolveTemplate, removeTrack, setTrackTiming, trackDraft, updateTrack } from "./tracks";
import { previewUrls, readPreviewVideos, reorderVideos } from "./media";
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
  const [media, setMedia] = useState<PreviewMedia>();
  const [mediaError, setMediaError] = useState("");
  const [mediaAttempt, setMediaAttempt] = useState(0);
  const [videoOrder, setVideoOrder] = useState([0, 1, 2]);
  const [baseline, setBaseline] = useState("");
  const [catalog, setCatalog] = useState<EffectAsset[]>(() => readCatalog());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [assetError, setAssetError] = useState("");
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
  const spriteCatalog = useSpriteCatalog(environment, (draft?.clips.length ?? 0) > 0);
  const preview = useMemo(() => {
    if (!draft || !media) return { timeline: undefined, error: "" };
    try { return { timeline: resolveTemplate(draft, previewContext(media, videoOrder)), error: "" }; }
    catch (reason) { return { timeline: undefined, error: reason instanceof Error ? reason.message : "时间规则无法应用到当前视频" }; }
  }, [draft, media, videoOrder]);
  const previewLength = preview.timeline?.duration ?? 0;
  // 占用文字或交界处的片段按本次预览计算区间，展示与保存共用，不改变草稿。
  const timedDraft = useMemo(() => draft && preview.timeline ? timeClips(draft, preview.timeline) : draft, [draft, preview.timeline]);
  const timedClips = timedDraft?.clips;
  // 所作用文字在当前预览中不显示时，字效也不显示，避免按原有时间出现在画面上。
  const hiddenClips = useMemo(() => draft && preview.timeline ? hiddenTextClips(draft, preview.timeline) : new Set<string>(), [draft, preview.timeline]);
  const draftTracks = draft?.tracks;
  const spriteClips = useMemo(() => (timedClips ?? []).map((clip) => {
    const asset = spriteCatalog.assets?.find((item) => item.id === clip.sprite_id);
    // 作用于文字的字效显示该文字对象的示例文字与关键词，合成时由总线换成实际标题或切片。
    const text = clip.text_target && draftTracks?.find((track) => track.target === clip.text_target);
    return {
      id: clip.id,
      spriteId: clip.sprite_id,
      aspect: asset?.aspect ?? 9 / 16,
      name: asset?.name ?? "Remotion 资产",
      start: clip.start,
      end: Math.min(previewLength, clip.start + (clip.duration ?? asset?.seconds ?? 0)),
      length: clip.duration ?? asset?.seconds ?? 0,
      slot: clipSlot(clip, asset),
      boundary: clip.transition_boundary,
      values: text && clip.text_target ? { ...clip.parameters, text: text.editor[clip.text_target], keywords: [exampleKeyword(text.editor, clip.text_target)].filter(Boolean) } : clip.parameters,
      transform: clip.transform,
      canvas: asset ? { width: asset.width, height: asset.height, box: asset.box, movable: transformFields(asset.kind).includes("x") } : undefined,
    };
  }).filter((clip) => clip.end > clip.start && !hiddenClips.has(clip.id)), [timedClips, hiddenClips, draftTracks, spriteCatalog.assets, previewLength]);
  const dirty = draft !== null && (current === null || JSON.stringify(draft) !== baseline);
  const selectedTrack = draft?.tracks.find((track) => track.id === target);
  const assetTrack = selectedTrack ?? draft?.tracks.find((track) => track.id === lastTextTrack.current);
  const selectedDraft = draft && selectedTrack ? trackDraft(draft, selectedTrack) : null;
  const selectedTransition = selectedTrack?.target === "transition" ? selectedTrack : undefined;
  const selectedBoundary = selectedTransition?.transition_boundary;
  // IMS 对象与 Remotion 片段共用一个选中状态，右栏只显示其中一种设置。
  const selectedClip = timedClips?.find((clip) => clip.id === target);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // 默认地址与弹窗均读取真实元数据；已选择来源时不再启动默认读取。
  useEffect(() => {
    if (!draft || media) return;
    const controller = new AbortController();
    setMediaError("");
    void readPreviewVideos(previewUrls(), controller.signal).then((sources) => {
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
        setMedia(undefined);
        setVideoOrder([0, 1, 2]);
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
  async function persist(nextSelection?: TemplateSelection) {
    if (!draft || !timedDraft || lock.current || loading) return;
    // 时间输入保留局部编辑值，写入前检查；模板配置继续由 saveTemplate 校验。
    const timingInputs = form.current?.querySelectorAll<HTMLInputElement>('[aria-label="轨道时间设置"] input');
    if (timingInputs && [...timingInputs].some((input) => !input.checkValidity())) { setInspectorTab("timing"); return; }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      // Remotion 片段是模板的 `remotion` 轨道，占用对象的片段带上本次预览的区间，与 IMS 对象在同一次请求里保存，要么都成功要么都不写入。
      const saved = await api.saveTemplate(timedDraft, current?.template_id, environment, current?.library);
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
                  if (asset.category === "vfx/normal" && draft.clips.some((clip) => clipSlot(clip, spriteCatalog.assets?.find((item) => item.id === clip.sprite_id)) === "vfx"))
                    throw new Error("画面特效最多一个，已添加的 Remotion 视频特效也占用这个名额，请先移除");
                  const result = addTrack(draft, asset, role, lastTextTrack.current ?? undefined);
                  setTarget(result.id);
                  const added = result.draft.tracks.find((track) => track.id === result.id)!;
                  if (isTextTarget(added.target)) { setTextTarget(added.target); lastTextTrack.current = added.id; }
                  return result.draft;
                }, true)} />
                <SpriteCatalog environment={environment} catalog={spriteCatalog} onAdd={(asset) => editTrack(() => addClip(draft, asset, spriteCatalog.assets ?? [], textTarget), true)} />
              </aside>}
              canvas={<main className="template-workspace-canvas h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <TemplatePreview draft={draft} media={media} resolved={preview.timeline} mediaError={mediaError || preview.error} onRetryMedia={() => setMediaAttempt((value) => value + 1)} onMediaChange={(next) => { setMedia(next); setMediaError(""); }} videoInputKey={`${environment}:${current?.template_id ?? draft.name}`} onCatalog={setCatalog} selectedId={target} onSelect={(id) => { if (draft.tracks.some((track) => track.id === id) || draft.clips.some((clip) => clip.id === id)) selectTarget(id); }} sprites={spriteClips} onSpriteMove={(id, x, y) => editTrack(() => setClipTransform(draft, id, { x, y }))}
                  videoOrder={videoOrder} onVideoMove={(source, destination) => {
                    try {
                      const next = reorderVideos(videoOrder, source, destination);
                      setVideoOrder(next); setError("");
                    } catch (reason) { setError(reason instanceof Error ? reason.message : "视频排列修改失败"); }
                  }} />
              </main>}
              inspector={<aside className="template-workspace-inspector h-full min-w-0 overflow-y-auto xl:border-l"><AppliedEffects draft={draft} catalog={catalog} resolved={preview.timeline} selected={target} onSelect={selectTarget} />
                <SpriteClips environment={environment} clips={timedClips ?? []} catalog={spriteCatalog} duration={preview.timeline?.duration} hidden={hiddenClips} selected={selectedClip?.id ?? null} onSelect={selectTarget} onRemove={(id) => editTrack(() => removeClip(draft, id))} />
                {selectedClip && <SpriteClipInspector key={selectedClip.id} clip={selectedClip} asset={spriteCatalog.assets?.find((item) => item.id === selectedClip.sprite_id)} duration={preview.timeline?.duration}
                  onParameter={(key, value) => editTrack(() => setClipParameter(draft, selectedClip.id, key, value))}
                  onTransform={(patch) => editTrack(() => setClipTransform(draft, selectedClip.id, patch))}
                  onStart={(start) => editTrack(() => moveClip(draft, selectedClip.id, start))}
                  onBind={(binding) => editTrack(() => bindClip(draft, selectedClip.id, binding))}
                  onRemove={() => { editTrack(() => removeClip(draft, selectedClip.id)); setTarget(null); }}
                  onClose={() => setTarget(null)} />}
                {selectedTransition && <TransitionInspector boundary={selectedBoundary!} track={selectedTransition} catalog={catalog} resolved={preview.timeline?.tracks[selectedTransition.id]}
                  onChoose={(asset) => editTrack(() => { if (asset) return addTrack(draft, asset, "transition", undefined, selectedBoundary).draft; setTarget(null); return removeTrack(draft, selectedTransition.id); })}
                  onDuration={(seconds) => editTrack(() => updateTrack(draft, selectedTransition.id, { editor: selectedTransition.editor, transition_duration_seconds: seconds }))}
                  onClose={() => setTarget(null)} />}
                {selectedTrack && selectedTrack.target !== "transition" && <TemplateInspector track={selectedTrack} draft={selectedDraft!} catalog={catalog} duration={previewLength} resolved={preview.timeline?.tracks[selectedTrack.id]} tab={inspectorTab} onTabChange={setInspectorTab} onTimingChange={(timing) => editTrack(() => setTrackTiming(draft, selectedTrack.id, timing))} onEffectChange={(next) => editTrack(() => updateTrack(draft, selectedTrack.id, next))} onRemove={() => editTrack(() => removeTrack(draft, selectedTrack.id))} onClose={() => setTarget(null)} />}
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
