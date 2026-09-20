/** 模板编辑工作区：接收主页选择，展示模板信息，协调效果编辑、保存和未保存切换保护。 */
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import * as api from "./api";
import { readCatalog } from "./sdk";
import { newDraft, toDraft, type Draft, type EffectAsset, type Template, type TextRole, type MasterVideo } from "./model";
import { EffectEditor } from "./EffectEditor";
import { AppliedEffects, EffectAssets } from "./EffectAssets";
import { isTextTarget } from "./effects";
import { addTrack, previewDuration, removeTrack, setTrackRange, setTrackTiming, trackDraft, updateTrack, withTracks } from "./tracks";
import { TrackTiming } from "./TrackTiming";
import { MasterVideoInput } from "./MasterVideoInput";
import { TemplatePreview } from "./TemplatePreview";
import type { TemplateSelection } from "./TemplateHome";

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
  const [notice, setNotice] = useState("");
  const [action, setAction] = useState<TemplateSelection | null>(null);
  const [openRequest, setOpenRequest] = useState<TemplateSelection | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [target, setTarget] = useState<string | null>("title");
  const [textTarget, setTextTarget] = useState<TextRole>("title");
  const lastTextTrack = useRef<string | null>("title");
  const lock = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const mounted = useRef(false);
  const handledSelection = useRef<TemplateSelection | null>(null);
  const dirty = draft !== null && (current === null || JSON.stringify(draft) !== baseline);
  const selectedTrack = draft?.tracks?.find((track) => track.id === target);
  const assetTrack = selectedTrack ?? draft?.tracks?.find((track) => track.id === lastTextTrack.current);
  const selectedDraft = draft && selectedTrack ? trackDraft(draft, selectedTrack) : draft;
  const assetDraft = draft && assetTrack ? trackDraft(draft, assetTrack) : draft;

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
        const next = withTracks(template ? toDraft(template) : {
          ...newDraft(),
          tracks: [],
          name: openRequest.templateId === null ? openRequest.name : "",
          description: openRequest.templateId === null ? openRequest.description : "",
        });
        setCurrent(template);
        setDraft(next);
        setMedia(undefined);
        setBaseline(JSON.stringify(next));
        setEnvironment(openRequest.environment);
        setTarget(next.tracks[0]?.id ?? null);
        const initialText = next.tracks.find((track) => isTextTarget(track.target));
        lastTextTrack.current = initialText?.id ?? null;
        setTextTarget(initialText && isTextTarget(initialText.target) ? initialText.target : "title");
        setNotice("");
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
    const selected = draft?.tracks?.find((track) => track.id === next);
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
    if (timingInputs && [...timingInputs].some((input) => !input.reportValidity())) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.saveTemplate(draft, current?.template_id, environment);
      if (!mounted.current) return;
      const next = withTracks(toDraft(saved));
      setCurrent(saved);
      setDraft(next);
      setBaseline(JSON.stringify(next));
      setNotice(`模板「${saved.name}」已保存`);
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
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      {openRequest && error && !action && <Button type="button" variant="outline" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>重试打开模板</Button>}
      {loading && <p role="status" className="text-sm text-muted-foreground">正在读取模板…</p>}
      {!draft ? (
        <section aria-label="模板编辑入口" className="space-y-4 rounded-xl border bg-card p-6">
          <p className="text-muted-foreground">请从主页选择已有模板或创建新模板。</p>
          <Button type="button" variant="outline" onClick={onHome}>前往主页</Button>
        </section>
      ) : (
        <form ref={form} className="overflow-hidden rounded-xl border bg-card" onSubmit={(event) => { event.preventDefault(); void persist(); }}>
          <fieldset disabled={busy || loading} className="min-w-0 disabled:opacity-60">
            <section aria-label="模板信息" className="flex flex-wrap items-start justify-between gap-4 border-b p-4">
              <dl className="min-w-0 flex-1 space-y-3">
                <div className="flex flex-wrap items-start gap-x-8 gap-y-3">
                  <div className="space-y-1"><dt className="text-xs text-muted-foreground">当前环境</dt><dd aria-label="当前环境" className="text-sm">{environment === "cloud" ? "云端" : "本地"}</dd></div>
                  <div className="min-w-0 space-y-1"><dt className="text-xs text-muted-foreground">模板名称</dt><dd aria-label="模板名称" className="break-all font-medium">{draft.name}</dd></div>
                </div>
                <div className="space-y-1"><dt className="text-xs text-muted-foreground">模板描述</dt><dd aria-label="模板描述" className="whitespace-pre-wrap break-all text-sm text-muted-foreground">{draft.description || "暂无模板描述"}</dd></div>
              </dl>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xs text-muted-foreground">{current ? dirty ? "有未保存的修改" : "已保存" : "新模板 · 尚未保存"}</span>
                <Button type="submit">{busy ? "正在保存…" : "保存模板"}</Button>
              </div>
            </section>
            <div className="grid min-w-0 items-start @min-[680px]:grid-cols-[220px_minmax(0,1fr)] @min-[1000px]:grid-cols-[220px_minmax(0,1fr)_280px]">
              <div className="min-w-0 @min-[680px]:border-r">
                <EffectAssets draft={assetDraft!} catalog={catalog} textTarget={textTarget} textEditor={draft.tracks?.find((track) => track.id === lastTextTrack.current)?.editor} onTextTarget={(role) => {
                  setTextTarget(role);
                  const text = draft.tracks?.find((track) => track.target === role);
                  setTarget(text?.id ?? null);
                  lastTextTrack.current = text?.id ?? null;
                }} onApply={setDraft} onAsset={(asset, role) => editTrack(() => {
                  const result = addTrack(draft, asset, role, lastTextTrack.current ?? undefined);
                  setTarget(result.id);
                  const added = result.draft.tracks!.find((track) => track.id === result.id)!;
                  if (isTextTarget(added.target)) { setTextTarget(added.target); lastTextTrack.current = added.id; }
                  return result.draft;
                })} />
              </div>
              <div className="min-w-0 border-y bg-muted/30 @min-[680px]:border-y-0">
                <MasterVideoInput key={`${environment}:${current?.template_id ?? draft.name}`} media={media} onChange={setMedia} />
                <TemplatePreview draft={draft} media={media} onCatalog={setCatalog} selectedId={target} onSelect={selectTarget} onRangeChange={(id, start, end) => editTrack(() => setTrackRange(draft, id, start, end, media))} />
                <AppliedEffects draft={draft} catalog={catalog} selected={target} onSelect={selectTarget} />
              </div>
              {selectedTrack && <div className="min-w-0 @min-[680px]:col-span-2 @min-[680px]:border-t @min-[1000px]:col-span-1 @min-[1000px]:border-l @min-[1000px]:border-t-0">
                <TrackTiming track={selectedTrack} duration={previewDuration(draft, media)} onChange={(timing) => editTrack(() => setTrackTiming(draft, selectedTrack.id, timing))} />
                <EffectEditor draft={selectedDraft!} catalog={catalog} onChange={(next) => editTrack(() => updateTrack(draft, selectedTrack.id, next))} target={selectedTrack.target} onRemove={() => editTrack(() => removeTrack(draft, selectedTrack.id))} onClose={() => setTarget(null)} />
              </div>}
            </div>
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
