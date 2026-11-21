/** 模板编辑工作区：接收主页选择，展示模板信息，协调效果编辑、保存和未保存切换保护。 */
import { SplitScreenEditor } from "./SplitScreenEditor";
import { defaultAvatarEffects } from "./avatarEffects";
import { AvatarCircleControls, AvatarEffectsEditor, AvatarMaskCard, AvatarPreviewControls } from "./AvatarEffectsEditor";
import { defaultAvatarPreview } from "./avatarPreview";
import { useTemplatePreviewModel } from "./useTemplatePreviewModel";
import { useUpdateBlocker } from "@/lib/update-guard";
import { beginDiagnostic, briefly } from "@/lib/diagnostics";
import { recovery } from "@/features/updates/recovery";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, CircleCheck, Pencil, SquarePen } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import * as api from "./api";
import { readCatalog } from "./sdk";
import { activeDraft, boundText, newDraft, toDraft, type Draft, type EffectAsset, type Template, type TextRole, type PreviewMedia } from "./model";
import { AppliedEffects, EffectAssets } from "./EffectAssets";
import { isTextTarget } from "./effects";
import { addTrack, removeTrack, setBilingual, setTrackTiming, trackDraft, updateTrack } from "./tracks";
import { bundledUrls, coverSource, defaultPreviewUrls, readPreviewVideos } from "./media";
import { compactTransitions, setTransitionDuration, transitionItems } from "./transitions";
import { TemplateInspector, TransitionInspector, type InspectorTab } from "./TemplateInspector";
import { releaseCoverTab, requestCoverTab } from "./coverCapture";
import { TemplatePreview, type CaptureCover } from "./TemplatePreview";
import { FrameMaskCard, FrameMaskPanel } from "./FrameMaskPanel";
import { FRAME_MASK_OBJECT_ID, newFrameMask } from "./frameMask";
import { SpriteCatalog, SpriteClips } from "./SpritePanels";
import { SpriteClipInspector } from "./SpriteClipInspector";
import { addClip, bindClip, clipSlot, moveClip, removeClip, setClipParameter, setClipTransform, timeClips } from "./clips";
import { useSpriteCatalog } from "./useSpriteCatalog";
import type { TemplateSelection } from "./TemplateHome";
import "./template-workspace.css";
import { Spinner } from "@/components/ui/spinner";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { debugLog, describe } from "@/lib/diagnostics";

/** 窗口宽度仅改变排列和调整权限，保留各栏组件、输入草稿与播放器实例。 */
function WorkspaceColumns({ assets, canvas, inspector }: { assets: ReactNode; canvas: ReactNode; inspector: ReactNode }) {
  const [desktop, setDesktop] = useState(() => window.innerWidth >= 1280);
  useEffect(() => {
    const update = () => setDesktop(window.innerWidth >= 1280);
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return <ResizablePanelGroup orientation="horizontal" disabled={!desktop} data-layout={desktop ? "desktop" : "narrow"} className="template-workspace-columns" style={{ height: desktop ? "100%" : "auto", minHeight: desktop ? 0 : undefined }}>
    <ResizablePanel id="assets" data-workspace-panel="assets" defaultSize="20%" minSize={240} maxSize="38%" className="min-w-0">{assets}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整资产与画布宽度" className="template-workspace-handle" />
    <ResizablePanel id="canvas" data-workspace-panel="canvas" defaultSize="58%" minSize={340} className="min-w-0">{canvas}</ResizablePanel>
    <ResizableHandle withHandle aria-label="调整画布与参数宽度" className="template-workspace-handle" />
    <ResizablePanel id="inspector" data-workspace-panel="inspector" defaultSize="22%" minSize={280} maxSize="42%" className="min-w-0">{inspector}</ResizablePanel>
  </ResizablePanelGroup>;
}

/** 导航与主页共享当前编辑状态，不包含可变草稿。 */
export interface EditingState { id: string | null; name: string; dirty: boolean }

/** 只有成功读取或明确放弃时才替换草稿；保存始终使用当前模板的 ID。 */
export function TemplateWorkspace({ selection = null, deletedTemplate = null, onHome, onOpenRemotion, onEditingState, onSaved, active = true }: {
  active?: boolean;
  onEditingState?: (state: EditingState | null) => void;
  /** 仅成功写入模板后通知主页刷新列表。 */
  onSaved?: () => void;
  selection?: TemplateSelection | null;
  deletedTemplate?: TemplateSelection | null;
  onHome: () => void;
  /** 回到字效工作区修改资产本身（颜色、动画等不在模板里调整的参数）。 */
  onOpenRemotion?: () => void;
}) {
  const [current, setCurrent] = useState<Template | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<{ field: "name" | "description"; value: string } | null>(null);
  const [media, setMedia] = useState<PreviewMedia>();
  const [mediaChanged, setMediaChanged] = useState(false);
  const [coverError, setCoverError] = useState("");
  const capture = useRef<CaptureCover>(null);
  const [mediaError, setMediaError] = useState("");
  const [mediaAttempt, setMediaAttempt] = useState(0);
  const [baseline, setBaseline] = useState("");
  const [catalog, setCatalog] = useState<EffectAsset[]>(() => readCatalog());
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [maskUploading, setMaskUploading] = useState(false);
  const [error, setError] = useState("");
  const [assetError, setAssetError] = useState("");
  const [action, setAction] = useState<TemplateSelection | null>(null);
  const [openRequest, setOpenRequest] = useState<TemplateSelection | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [assetSource, setAssetSource] = useState("effects");
  const [target, setTarget] = useState<string | null>("title");
  const inspectorId = useId();
  const [inspectorPage, setInspectorPage] = useState<"objects" | "layout">("objects");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("timing");
  const [textTarget, setTextTarget] = useState<TextRole>("title");
  const lastTextTrack = useRef<string | null>("title");
  const lock = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const mounted = useRef(false);
  const handledSelection = useRef<TemplateSelection | null>(null);
  const spriteCatalog = useSpriteCatalog((draft?.clips.length ?? 0) > 0, active);
  const { hiddenClips, visibleDraft, previewSelection, previewDraft, preview, previewLength, spriteClips, coverAssetsReady, setTransitionRandom } = useTemplatePreviewModel(draft, media, spriteCatalog.assets);
  const circleAvatar = !!draft?.split_screen?.enabled && draft.split_screen.layout === "circle";
  const avatarPreview = draft?.avatar_preview ?? defaultAvatarPreview;
  const avatarPreviewControls = <AvatarPreviewControls source={avatarPreview.source} center={target === "avatar-mask" || circleAvatar ? avatarPreview : undefined}
    onSourceChange={(source) => { if (draft) editTrack(() => ({ ...draft, avatar_preview: { ...defaultAvatarPreview, source } })); }}
    onCenterChange={(point) => { if (draft) editTrack(() => ({ ...draft, avatar_preview: { ...avatarPreview, ...point } })); }} />;
  const timedDraft = useMemo(() => draft && preview.timeline ? timeClips(draft, preview.timeline) : draft, [draft, preview.timeline]);
  const timedClips = timedDraft ? activeDraft(timedDraft).clips : undefined;
  const dirty = draft !== null && (current === null || mediaChanged || JSON.stringify(draft) !== baseline || (editing !== null && editing.value !== draft[editing.field]));
  useUpdateBlocker("模板正在处理", busy || loading || maskUploading);
  useEffect(() => { recovery.template = current ? { templateId: current.template_id } : null; }, [current]);
  useEffect(() => { onEditingState?.(draft ? { id: current?.template_id ?? null, name: draft.name, dirty } : null); }, [draft?.name, !!draft, current?.template_id, dirty, onEditingState]);
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
  // 中英文对象共用双语字效时，任何一个对象的面板都不能单独关闭翻译。
  const bilingualRequired = !!bilingualTarget && draft?.clips.some((clip) => clip.text_target === bilingualTarget && clip.language !== "en"
    && spriteCatalog.assets?.some((asset) => asset.id === clip.sprite_id && asset.bilingual)) === true;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // 主页删除模板后取消对应读取、清空对应编辑区，删除其他模板不影响当前草稿。
  useEffect(() => {
    if (!deletedTemplate) return;
    if (openRequest?.templateId === deletedTemplate.templateId) {
      setOpenRequest(null); setLoading(false); setError("");
    }
    if (!current || deletedTemplate.templateId !== current.template_id) return;
    setCurrent(null); setDraft(null); setEditing(null); setBaseline(""); setError("");
  }, [deletedTemplate, current, openRequest]);

  // 默认地址（本机保存的本地视频优先）与弹窗均读取真实元数据；已选择来源时不再启动默认读取。
  useEffect(() => {
    if (!draft || media) return;
    const controller = new AbortController();
    setMediaError("");
    void defaultPreviewUrls().then((urls) => readPreviewVideos(urls, controller.signal)).then((sources) => {
      if (!controller.signal.aborted) setMedia(sources);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setMediaError(reason instanceof Error ? reason.message : "预览视频读取失败");
      debugLog("模板编辑", `读取预览视频失败：${describe(reason)}`);
    });
    return () => controller.abort();
  }, [!!draft, draft?.name, current?.template_id, media, mediaAttempt]);

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
    if (selection.templateId && selection.templateId === current?.template_id) return;
    if (dirty) setAction(selection);
    else setOpenRequest(selection);
  }, [selection, loading, busy, current, dirty]);

  // 每次读取拥有独立取消信号；迟到响应、卸载及 StrictMode 重建都不能替换当前草稿。
  useEffect(() => {
    if (!openRequest) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const read = async () => {
      try {
        const template = openRequest.templateId !== null
          ? await api.getTemplate(openRequest.templateId, controller.signal)
          : null;
        if (controller.signal.aborted) return;
        const next = template ? toDraft(template) : {
          ...newDraft(),
          name: openRequest.templateId === null ? openRequest.name : "",
          description: openRequest.templateId === null ? openRequest.description : "",
        };
        setCurrent(template);
        setCoverError(""); setMediaChanged(false);
        setDraft(next);
        setEditing(null);
        setMedia(undefined);
        setBaseline(JSON.stringify(next));
        setTarget(next.tracks[0]?.id ?? null);
        setInspectorPage("objects");
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
    setInspectorPage("objects");
    setTarget(next);
    const clip = draft?.clips.find((item) => item.id === next);
    const selected = draft?.tracks.find((track) => track.id === next) ?? (draft && clip ? boundText(draft.tracks, clip) : undefined);
    if (selected && isTextTarget(selected.target)) { setTextTarget(selected.target); lastTextTrack.current = selected.id; }
  }

  /** 添加资产失败时使用提示框；参数错误显示在工作区，失败时保留当前草稿。 */
  function editTrack(change: () => Draft, addingAsset = false) {
    try { setDraft(change()); setError(""); if (addingAsset) setInspectorPage("objects"); }
    catch (reason) {
      const message = reason instanceof Error ? reason.message : "轨道修改失败";
      if (addingAsset) setAssetError(message);
      else setError(message);
    }
  }

  /** 先在点击事件中取得浏览器授权；桌面走原生截图，失败不影响模板写入。 */
  function beginSnapshot() {
    return requestCoverTab().then(stream => ({ stream, error: "" }), reason => ({ stream: undefined, error: String(reason) }));
  }

  /** 已保存模板的封面独立发布，失败的图片仅在内存中等待同一版本重传。 */
  async function publish(saved: Template, tab?: ReturnType<typeof beginSnapshot>) {
    if (!saved.cover) throw new Error("请升级模板服务并重试");
    const { revision } = saved.cover;
    const published = await api.publishCover(saved.template_id, revision, async () => {
      const grant = await tab;
      if (grant?.error) throw new Error(grant.error);
      if (!capture.current) throw new Error("请先加载预览视频");
      return capture.current(grant?.stream);
    }, saved.cover.publishedRevision === revision);
    return { ...saved, cover: { ...saved.cover, url: published.url, revision: published.revision, publishedRevision: published.revision } };
  }

  /** 取消确认后继续保留当前草稿。 */
  function cancelOpening() {
    setAction(null); setOpenRequest(null); setError("");
  }

  /** 单独重试已保存的画面；旧素材丢失时须重新选择并保存，避免将当前草稿发布到旧版本。 */
  async function retryCover() {
    if (!current || lock.current) return;
    if (dirty) { setCoverError("请先保存当前修改"); return; }
    const saved = current.cover ? current : { ...current, cover: { revision: current.template_id, source: "builtin", publishedRevision: "", url: "" } };
    const cached = api.hasCoverBlob(saved.template_id, saved.cover!.revision);
    const tab = cached ? undefined : beginSnapshot();
    lock.current = true; setBusy(true);
    try {
      if (!cached && saved.cover!.source !== "unavailable" && await coverSource(media) !== saved.cover!.source) {
        if (saved.cover!.source !== "builtin") throw new Error("原预览素材不可用，请重新选择视频并保存");
        // 补图或重试内置样片时，不受本机上次为其他模板选择的素材影响。
        const sources = await readPreviewVideos(bundledUrls(), new AbortController().signal);
        if (!mounted.current) return;
        setMediaError("");
        setMedia(sources);
        await new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done())));
      }
      setCurrent(await publish(saved, tab)); setCoverError(""); onSaved?.();
    } catch (reason) { setCoverError(reason instanceof Error ? reason.message : "请重试"); }
    finally {
      if (tab) void tab.then(({ stream }) => releaseCoverTab(stream));
      lock.current = false; setBusy(false);
    }
  }

  /** 成功保存响应建立新基线；失败保留原草稿和待切换目标，不自动重发。 */
  async function persist(nextSelection?: TemplateSelection, savingDraft = draft) {
    const trace = beginDiagnostic("前端模板", "保存模板");
    if (!savingDraft || lock.current || loading || maskUploading) {
      trace.finish("未执行：没有模板草稿，或仍在加载、上传、保存", "警告");
      return;
    }
    if (editing?.field === "name" && !editing.value.trim()) { trace.finish("校验失败：模板名称为空", "警告"); setError("请输入模板名称"); return; }
    // 时间输入保留局部编辑值，写入前检查；模板配置继续由 saveTemplate 校验。
    const timingInputs = form.current?.querySelectorAll<HTMLInputElement>('[aria-label="轨道时间设置"] input');
    if (timingInputs && [...timingInputs].some((input) => !input.checkValidity())) { trace.finish("校验失败：轨道时间无效，未提交", "警告"); setInspectorTab("timing"); return; }
    const transitionInput = form.current?.querySelector<HTMLInputElement>('[aria-label="转场时间设置"] input[type="number"]');
    if (transitionInput && !transitionInput.checkValidity()) { trace.finish("校验失败：转场时间无效，未提交", "警告"); transitionInput.reportValidity(); return; }
    // 数字人输入有独立字符串草稿；按钮、快捷键和元数据自动保存都不能把未完成输入当成旧有效值保存。
    const avatarInput = [...(form.current?.querySelectorAll<HTMLInputElement>('input[data-avatar-effect]') ?? [])].find((input) => !input.checkValidity());
    if (avatarInput) { trace.finish("校验失败：数字人参数无效，未提交", "警告"); avatarInput.reportValidity(); return; }
    const visual = !current || mediaChanged || JSON.stringify({ ...savingDraft, name: "", description: "" })
      !== JSON.stringify({ ...toDraft(current), name: "", description: "" });
    const tab = visual && media ? beginSnapshot() : undefined;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      // Remotion 字效保存文字区间，转场保留自身时长，与 IMS 对象在同一次请求里保存。
      const saveDraft = preview.timeline ? timeClips(savingDraft, preview.timeline, "save") : savingDraft;
      const source = visual ? await coverSource(media).catch(() => "unavailable") : undefined;
      trace.step("前端校验通过，提交模板与轨道配置");
      let saved = await trace.run(() => api.saveTemplate(
        setTransitionDuration(saveDraft, savingDraft.transition_duration_seconds), current?.template_id, !!current?.frame_mask, source,
      ));
      trace.step(`模板 ${saved.template_id} 已保存`);
      if (!mounted.current) { trace.finish("保存已完成，页面已卸载，跳过界面更新"); return; }
      if (nextSelection) setAction(null);
      if (visual) {
        trace.step("画面已变化，开始更新封面");
        try { saved = await trace.run(() => publish(saved, tab)); setCoverError(""); trace.step("封面更新完成"); }
        catch (reason) { trace.step(`模板已保存，但封面更新失败：${briefly(reason)}`, "警告"); setCoverError(reason instanceof Error ? reason.message : "请重试"); }
      }
      if (!mounted.current) { trace.finish("页面已卸载，保留已写入结果"); return; }
      setMediaChanged(false);
      const next = toDraft(saved);
      setCurrent(saved);
      setDraft(next);
      setBaseline(JSON.stringify(next));
      onSaved?.();
      toast.success(`模板「${saved.name}」已保存`);
      trace.finish("草稿基线已更新，保存完成");
      if (nextSelection) { setOpenRequest(nextSelection); setAttempt((value) => value + 1); }
    } catch (reason) {
      trace.finish(`保存失败，保留草稿：${briefly(reason)}`, "错误");
      if (mounted.current) setError(reason instanceof Error ? reason.message : "保存失败，请重试");
    } finally {
      if (tab) void tab.then(({ stream }) => releaseCoverTab(stream));
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

  // 双语状态仍归文字对象所有，两种外观面板共用同一组控件。
  const bilingualControls = draft && bilingualTarget && <div className="space-y-2 border-b px-4 py-3">
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={!!english && english.enabled !== false} disabled={bilingualRequired && !!english && english.enabled !== false} onChange={(event) => {
      const enabled = event.target.checked;
      if (!enabled && bilingualRequired) return;
      editTrack(() => {
        const next = setBilingual(draft, bilingualTarget, enabled);
        if (!enabled && selectedText?.language === "en") {
          const chinese = next.tracks.find((track) => track.target === bilingualTarget && track.language !== "en")!;
          setTarget(chinese.id); lastTextTrack.current = chinese.id;
        }
        return next;
      });
    }} />中英双语</label>
    <p className="text-[11px] text-muted-foreground">{bilingualRequired
      ? "双语字效已自动启用中英双语，合成时会翻译实际文案。若要关闭，请先移除该双语字效。"
      : "开启后，合成时会翻译实际文案并显示英文；关闭时不翻译此对象，也不输出英文。"}</p>
    {selectedText?.language === "en" && <p className="text-[11px] text-muted-foreground">时间跟随中文{bilingualTarget === "title" ? "标题" : "字幕"}，合成时自动翻译实际文案。</p>}
  </div>;

  return (
    <div className={cn("@container min-w-0", draft ? "space-y-3" : "flex min-h-full flex-col gap-3")}>
      {current && (coverError || (current.cover && current.cover.revision !== current.cover.publishedRevision)) && <div role="alert" className="flex items-center gap-2 text-sm text-warning">
        <span>封面更新失败{coverError ? ` · ${coverError}` : ""}</span><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void retryCover()}>重试</Button>
      </div>}
      {error && !action && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
      {openRequest && error && !action && <Button type="button" variant="outline" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>重试打开模板</Button>}
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner />正在读取模板…</p>}
      {!draft ? !openRequest && (
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
              <div className="flex min-w-0 flex-1 items-center gap-4"><Button type="button" variant="ghost" size="icon-sm" aria-label="返回主页" onClick={onHome}><ArrowLeft className="size-4" /></Button>
                <dl className="min-w-0 flex-1 space-y-1">
                  {(["name", "description"] as const).map((field) => {
                    const label = field === "name" ? "模板名称" : "模板描述";
                    return <div key={field} className="flex h-9 w-full min-w-0 items-center gap-2">
                      <dt className="sr-only">{label}</dt>
                      <dd className={cn("min-w-0", editing?.field === field && (field === "description" ? "w-[max(25%,12rem)] max-w-full" : "flex-1"))}>
                        {editing?.field === field ? <Input autoFocus aria-label={label} value={editing.value}
                          required={field === "name"} maxLength={field === "name" ? 100 : 1000} className="h-8 w-full"
                          onChange={(event) => setEditing({ field, value: event.target.value })} onBlur={saveMetadata}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") { event.preventDefault(); setEditing(null); setError(""); }
                            if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.blur(); }
                          }} />
                          : <span aria-label={label} className={field === "name" ? "sketch-text block truncate text-[28px]" : "block truncate text-[11px] text-muted-foreground"}>{draft[field] || "暂无模板描述"}</span>}
                      </dd>
                      {editing?.field !== field && <Hint label={`编辑${label}`}><Button type="button" variant="ghost" size="icon-xs" aria-label={`编辑${label}`}
                        onClick={() => { setEditing({ field, value: draft[field] }); setError(""); }}><Pencil className="size-3" aria-hidden="true" /></Button></Hint>}
                    </div>;
                  })}
                </dl>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <span className="template-workspace-status flex items-center gap-2 text-[11px] text-muted-foreground"><span className={`size-1.5 rounded-full ${dirty ? "bg-warning" : "bg-success"}`} />{current ? dirty ? "有未保存的修改" : "已保存" : "新模板 · 尚未保存"}</span>
                <Button data-update-draft={`模板 · ${draft.name || "未命名模板"}`} data-update-invalid-only={!dirty || undefined} type="submit" disabled={maskUploading} className="template-workspace-save h-9 gap-2 rounded-lg px-4 text-xs">{busy ? <Spinner /> : <CircleCheck className="size-4" aria-hidden="true" />}{busy ? "正在保存…" : "保存模板"}</Button>
              </div>
            </section>
            <WorkspaceColumns
              assets={<aside className="template-workspace-assets h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <div className="flex h-14 items-center gap-1 border-b border-line px-3" role="tablist" aria-label="素材来源">
                  <button type="button" role="tab" aria-selected={assetSource === "effects"} className="studio-source-tab" onClick={() => setAssetSource("effects")}>特效素材</button>
                  <button type="button" role="tab" aria-selected={assetSource === "ai"} className="studio-source-tab" onClick={() => { setAssetSource("ai"); spriteCatalog.refresh(); }}>AI 资产</button>
                  <button type="button" role="tab" aria-selected={assetSource === "masks"} className="studio-source-tab" onClick={() => setAssetSource("masks")}>蒙版</button>
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
                <div hidden={assetSource !== "ai"}><SpriteCatalog catalog={spriteCatalog} placed={new Set(draft.clips.map((clip) => clip.sprite_id))} onAdd={(asset) => editTrack(() =>
                  addClip(draft, asset, spriteCatalog.assets ?? [], textTarget, draft.tracks.find((track) => track.id === lastTextTrack.current)?.language), true)} /></div>
                <div hidden={assetSource !== "masks"}><FrameMaskCard mask={draft.frame_mask} onSelect={() => {
                  if (!draft.frame_mask) editTrack(() => ({ ...draft, frame_mask: newFrameMask() }));
                  selectTarget(FRAME_MASK_OBJECT_ID);
                }} /><AvatarMaskCard onSelect={() => {
                  if (!draft.avatar_effects?.openingEnabled) editTrack(() => ({ ...draft, avatar_effects: { ...defaultAvatarEffects(), ...draft.avatar_effects, openingEnabled: true } }));
                  selectTarget("avatar-mask");
                }} /></div>
              </aside>}
              canvas={<main className="template-workspace-canvas h-full min-w-0 overflow-y-auto border-b xl:border-b-0">
                <TemplatePreview captureRef={capture} coverAssetsReady={coverAssetsReady} draft={previewDraft!} media={media} avatarSource={avatarPreview.source} previewCenter={avatarPreview} resolved={preview.timeline} mediaError={mediaError || preview.error} onRetryMedia={() => setMediaAttempt((value) => value + 1)} onMediaChange={(next) => { if (media?.every((item, i) => item.url === next[i].url)) return; setMediaChanged(true); setMedia(next); setMediaError(""); if (draft.avatar_preview) editTrack(() => ({ ...draft, avatar_preview: { ...avatarPreview, x: 50, y: 50 } })); }} videoInputKey={current?.template_id ?? draft.name} onCatalog={setCatalog} selectedId={effectiveTarget} selectionTargets={previewSelection?.targets} onSelect={(id) => { selectTarget(previewSelection?.targets.get(id) ?? id); }} sprites={spriteClips} onSpriteMove={(id, x, y) => editTrack(() => setClipTransform(draft, previewSelection?.targets.get(id) ?? id, { x, y }))}
                />
              </main>}
              inspector={<aside className="template-workspace-inspector relative h-full min-w-0 overflow-y-auto xl:border-l">
                <div role="tablist" aria-label="模板设置分类" className="grid grid-cols-2 border-b px-4">
                  {([["objects", "画面对象"], ["layout", "画面布局"]] as const).map(([page, label]) => <button key={page} type="button" role="tab" id={`${inspectorId}-${page}`} aria-controls={`${inspectorId}-panel-${page}`} aria-selected={inspectorPage === page} onClick={() => setInspectorPage(page)} className="border-b-2 border-transparent px-1 py-3 text-xs text-muted-foreground data-[selected=true]:border-primary data-[selected=true]:text-primary" data-selected={inspectorPage === page}>{label}</button>)}
                </div>
                <div role="tabpanel" id={`${inspectorId}-panel-objects`} aria-labelledby={`${inspectorId}-objects`} hidden={inspectorPage !== "objects"}>
                <AppliedEffects draft={visibleDraft!} catalog={catalog} sprites={spriteCatalog.assets} resolved={preview.timeline} selected={selectedText?.id ?? target} onSelect={selectTarget} />
                <SpriteClips clips={(timedClips ?? []).filter((clip) => !boundText(draft.tracks, clip))} catalog={spriteCatalog} duration={preview.timeline?.duration} hidden={hiddenClips} selected={selectedClip?.id ?? null} onSelect={selectTarget} onRemove={(id) => editTrack(() => removeClip(draft, id))} />
                {target === FRAME_MASK_OBJECT_ID && draft.frame_mask && <FrameMaskPanel key={current?.template_id ?? "new"} mask={draft.frame_mask} onUploadingChange={setMaskUploading}
                  onChange={(frame_mask) => editTrack(() => ({ ...draft, frame_mask }))}
                  onRemove={() => editTrack(() => removeTrack(draft, FRAME_MASK_OBJECT_ID))} onClose={() => setTarget(null)} />}
                {target === "avatar-mask" && draft.avatar_effects?.openingEnabled && <AvatarEffectsEditor value={draft.avatar_effects} onChange={(avatar_effects) => editTrack(() => ({ ...draft, avatar_effects }))} onClose={() => setTarget(null)}
                  onRemove={() => editTrack(() => ({ ...draft, avatar_effects: { ...draft.avatar_effects!, openingEnabled: false } }))}>{avatarPreviewControls}</AvatarEffectsEditor>}
                {selectedClip && <SpriteClipInspector key={selectedClip.id} clip={selectedClip} asset={spriteCatalog.assets?.find((item) => item.id === selectedClip.sprite_id)} duration={preview.timeline?.duration}
                  appearanceControls={bilingualControls} textTrack={clipText} textTracks={visibleDraft!.tracks} textResolved={clipText && preview.timeline?.tracks[clipText.id]} onOpenRemotion={onOpenRemotion}
                  onSample={(value) => clipText && editTrack(() => {
                    const role = clipText.target as "title" | "subtitle";
                    const edit = trackDraft(draft, clipText);
                    return updateTrack(draft, clipText.id, { ...edit, editor: { ...edit.editor, [role]: value, ...(role === "title" ? { titleKeyword: "" } : {}) } });
                  })}
                  onEnglishSample={(value) => clipText && editTrack(() => {
                    // 双语字效下栏的英文示例写进同一用途的英文文字对象，合成时同样由切片译文替换。
                    const role = clipText.target as "title" | "subtitle";
                    const english = draft.tracks.find((track) => track.target === role && track.language === "en");
                    if (!english) throw new Error("还没有英文文字对象，请先开启中英双语");
                    const edit = trackDraft(draft, english);
                    return updateTrack(draft, english.id, { ...edit, editor: { ...edit.editor, [role]: value, ...(role === "title" ? { titleKeyword: "" } : {}) } });
                  })}
                  onTextTiming={(timing) => clipText && editTrack(() => setTrackTiming(draft, clipText.id, timing))}
                  onParameter={(key, value) => editTrack(() => setClipParameter(draft, selectedClip.id, key, value))}
                  onTransform={(patch) => editTrack(() => setClipTransform(draft, selectedClip.id, patch))}
                  onStart={(start) => editTrack(() => moveClip(draft, selectedClip.id, start))}
                  onBind={(binding) => editTrack(() => {
                    const next = bindClip(draft, selectedClip.id, binding, spriteCatalog.assets ?? []);
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
                {selectedTrack && !selectedClip && selectedTrack.target !== "transition" && <TemplateInspector appearanceControls={bilingualControls} track={selectedTrack} draft={selectedDraft!} catalog={catalog} duration={previewLength} resolved={preview.timeline?.tracks[selectedTrack.id]} tab={inspectorTab} onTabChange={setInspectorTab} onTimingChange={(timing) => editTrack(() => setTrackTiming(draft, selectedTrack.id, timing))} onEffectChange={(next) => editTrack(() => updateTrack(draft, selectedTrack.id, next))} sprites={spriteCatalog.assets} onNeedSprites={spriteCatalog.peek} onBindSprite={(spriteId) => editTrack(() => {
                  const asset = spriteCatalog.assets?.find((item) => item.id === spriteId);
                  if (!asset) return draft;
                  const role = selectedTrack.target === "title" ? "title" : "subtitle";
                  // 绑定后右栏改由字效设置接管，用户随即在「外观与效果」里换字体。
                  const next = addClip(draft, asset, spriteCatalog.assets ?? [], role, selectedTrack.language);
                  setTarget(next.clips.at(-1)!.id);
                  setTextTarget(role);
                  lastTextTrack.current = selectedTrack.id;
                  return next;
                }, true)} onRemove={() => editTrack(() => removeTrack(draft, selectedTrack.id))} onClose={() => setTarget(null)} />}
                </div>
                <div role="tabpanel" id={`${inspectorId}-panel-layout`} aria-labelledby={`${inspectorId}-layout`} hidden={inspectorPage !== "layout"}>
                <SplitScreenEditor value={draft.split_screen} removeBackground={draft.avatar_effects?.removeBackground} onRemoveBackgroundChange={(removeBackground) => editTrack(() => ({ ...draft, avatar_effects: { ...defaultAvatarEffects(), ...draft.avatar_effects, removeBackground } }))} onChange={(split_screen) => editTrack(() => ({ ...draft, split_screen,
                  ...(split_screen.layout !== (draft.split_screen?.layout ?? "split") ? { avatar_effects: { ...defaultAvatarEffects(), ...draft.avatar_effects, removeBackground: split_screen.layout === "overlay" } } : {}),
                }))}>{draft.split_screen?.enabled && <>
                  {circleAvatar && <AvatarCircleControls value={draft.avatar_effects} onChange={(avatar_effects) => editTrack(() => ({ ...draft, avatar_effects }))} />}
                  {avatarPreviewControls}
                </>}</SplitScreenEditor>
                </div>
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
      <Dialog open={!!action} onOpenChange={(open) => { if (!open && !busy && !loading) cancelOpening(); }}>
        <DialogContent showCloseButton={!busy && !loading}>
          <DialogHeader><DialogTitle>保存当前修改？</DialogTitle><DialogDescription>切换前可以保存当前配置、放弃修改，或取消切换。</DialogDescription></DialogHeader>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy || loading} onClick={cancelOpening}>取消</Button>
            <Button type="button" variant="outline" disabled={busy || loading} onClick={() => { setOpenRequest(action); setAttempt((value) => value + 1); }}>放弃修改</Button>
            <Button type="button" disabled={busy || loading || maskUploading} onClick={() => { if (action) void persist(action); }}>保存并切换</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
