/** 操练场由固定场景与模板发起异步合成；保存当前状态，串行查询并播放最后成功的成片。 */
import { useEffect, useId, useRef, useState } from "react";
import { Clapperboard, Film, LoaderCircle, Play, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { apiBase } from "@/lib/api-base";
import { listTemplates } from "@/features/templates/api";
import type { TemplateSummary } from "@/features/templates/model";
import { createPlayground, getComposition, type CompositionTask } from "./api";
import { emptyPlayground, loadPlayground, savePlayground, type PlaygroundState } from "./playgroundState";

/** 直接显示真实阶段，不根据等待时间编造完成比例。 */
const stages: Record<string, string> = {
  queued: "等待合成", template: "读取模板", assembling: "生成画面与字效", submitting: "提交云端合成",
  rendering: "正在 IMS 渲染", uploading: "正在获取成片并转存 ZOS", completed: "合成完成", failed: "合成失败",
};
const steps = ["queued", "assembling", "submitting", "rendering", "uploading", "completed"];
const failureStages: Record<string, string> = { template: "queued", remotion_render: "assembling", playback: "uploading", zos_upload: "uploading" };

/** 网络查询失败与任务失败分别显示，保留再次查询的机会。 */
function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/** 离开页签暂停播放器；工作区保留挂载，合成查询和本地保存继续完成。 */
export function PlaygroundWorkspace({ active = true }: { active?: boolean }) {
  const address = apiBase();
  const id = useId();
  const [saved, setSaved] = useState<PlaygroundState>(emptyPlayground);
  const current = useRef(saved);
  const [key, setKey] = useState("");
  const currentKey = useRef("");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [templateError, setTemplateError] = useState("");
  const [error, setError] = useState("");
  const [storageError, setStorageError] = useState("");
  const [queryError, setQueryError] = useState<{ message: string; missing: boolean } | null>(null);
  const [querying, setQuerying] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [reload, setReload] = useState(0);
  const [queryAttempt, setQueryAttempt] = useState(0);
  const [mediaError, setMediaError] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const mounted = useRef(true);
  const running = saved.task?.status === "queued" || saved.task?.status === "processing";
  const selected = templates.find((template) => template.template_id === saved.styleId);
  const stage = saved.task?.error?.stage || saved.task?.stage || "queued";
  const step = Math.max(0, steps.indexOf(failureStages[stage] || stage)) + 1;

  /** 内存立即更新，落盘按顺序完成；切换后端后的旧响应只保存到旧作用域。 */
  async function persist(next: PlaygroundState, scope = currentKey.current) {
    if (mounted.current && scope === currentKey.current) {
      current.current = next;
      setSaved(next);
    }
    try {
      await savePlayground(scope, next);
      if (mounted.current && scope === currentKey.current) setStorageError("");
    } catch (reason) {
      if (mounted.current && scope === currentKey.current) setStorageError(`当前状态未保存：${message(reason)}`);
    }
  }

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    currentKey.current = "";
    current.current = emptyPlayground();
    setSaved(current.current);
    setKey("");
    setError("");
    setQueryError(null);
    setQuerying(false);
    setQueryAttempt(0);
    setStorageError("");
    void loadPlayground(address).then((restored) => {
      if (cancelled) return;
      currentKey.current = restored.key;
      current.current = restored.state;
      setSaved(restored.state);
      setKey(restored.key);
    }).catch((reason) => { if (!cancelled) setStorageError(`无法恢复操练场：${message(reason)}`); });
    return () => { cancelled = true; };
  }, [address]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setTemplateError("");
    void listTemplates(controller.signal).then((items) => { if (!controller.signal.aborted) setTemplates(items); })
      .catch((reason) => { if (!controller.signal.aborted) setTemplateError(message(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [address, reload]);

  useEffect(() => {
    if (!key || !saved.task || (!running && queryAttempt === 0)) return;
    const taskId = saved.task.taskId;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setQueryError(null);
    /** 上一次查询完成后再排下一次，卸载、切换任务或服务时停止。 */
    async function poll() {
      setQuerying(true);
      try {
        const task = await getComposition(taskId, controller.signal, address);
        if (controller.signal.aborted) return;
        const next = { ...current.current, task };
        if (task.status === "succeeded" && task.result) {
          next.video = { ...task.result, taskId, styleId: next.taskStyleId, templateName: next.taskTemplateName };
        }
        if (JSON.stringify(next) !== JSON.stringify(current.current)) await persist(next, key);
        if (!controller.signal.aborted && (task.status === "queued" || task.status === "processing")) timer = setTimeout(poll, 15000);
      } catch (reason) {
        if (!controller.signal.aborted) setQueryError({ message: message(reason), missing: reason instanceof Error && "status" in reason && reason.status === 404 });
      } finally {
        if (!controller.signal.aborted) setQuerying(false);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, saved.task?.taskId, address, queryAttempt]);

  useEffect(() => { if (!active) video.current?.pause(); }, [active]);
  useEffect(() => { setMediaError(false); }, [saved.video?.videoUrl]);

  /** 提交只执行一次；即使切换后端，也将返回的任务 ID 保存到发起时的记录。 */
  async function submit() {
    if (!key || !selected || running || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    const previous = current.current;
    try {
      const taskId = await createPlayground("standard", previous.styleId, address);
      const task: CompositionTask = { taskId, status: "queued", stage: "queued", result: null, error: null, createdAt: "", updatedAt: "" };
      await persist({ ...previous, task, taskStyleId: previous.styleId, taskTemplateName: selected.name }, key);
    } catch (reason) {
      if (mounted.current && key === currentKey.current) setError(message(reason));
    } finally {
      submittingRef.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  return <div className="mx-auto flex min-h-full max-w-[1600px] flex-col px-6 py-8 sm:px-10">
    <header className="mb-7"><div className="mb-3 flex items-center gap-2 text-xs text-fg-subtle"><Clapperboard className="size-4" />视频合成</div>
      <h1 className="text-[26px] font-semibold tracking-tight">操练场</h1><p className="mt-2 text-sm text-fg-muted">用同一份内容，试出最合适的模板。</p></header>
    <div className="grid flex-1 items-start gap-6 xl:grid-cols-[minmax(340px,440px)_minmax(0,1fr)]">
      <section aria-label="合成设置" className="rounded-2xl border border-line bg-surface p-6">
        <h2 className="text-base font-semibold">合成设置</h2>
        <fieldset className="mt-6"><legend className="mb-3 text-sm font-medium">内容类型</legend>
          <div className="grid grid-cols-3 gap-2">
            <button type="button" aria-pressed="true" className="rounded-xl border border-fg bg-raised px-2 py-3 text-sm font-medium">IP口播<span className="mt-1 block text-[11px] font-normal text-fg-muted">固定样本</span></button>
            {["情景展示", "新闻体"].map((label) => <button key={label} type="button" disabled className="rounded-xl border border-line px-2 py-3 text-sm text-fg-subtle opacity-60">{label}<span className="mt-1 block text-[11px]">待初始化</span></button>)}
          </div>
        </fieldset>
        <div className="mt-7"><div className="mb-2 flex items-center justify-between"><label htmlFor={id} className="text-sm font-medium">styleId（模板）</label>
          <Button type="button" variant="ghost" size="sm" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCw className="size-3.5" />刷新</Button></div>
          <Select value={saved.styleId} onValueChange={(styleId) => { void persist({ ...current.current, styleId }); }} disabled={!key || loading || !!templateError || submitting || running}>
            <SelectTrigger id={id} className="w-full min-w-0 data-[size=default]:h-auto min-h-9 *:data-[slot=select-value]:line-clamp-none"><SelectValue placeholder={loading ? "正在读取模板…" : "请选择模板"} /></SelectTrigger>
            <SelectContent>{templates.map((template) => <SelectItem key={template.template_id} value={template.template_id} disabled={!!template.validation_error}><span className="min-w-0 whitespace-normal break-words text-left">{template.template_id}（{template.name}）</span></SelectItem>)}</SelectContent>
          </Select>
          {!loading && !templateError && !templates.length && <p className="mt-2 text-xs text-fg-muted">还没有已保存的模板，请先在模板编辑中保存。</p>}
          {!loading && !templateError && saved.styleId && !selected && <p role="alert" className="mt-2 text-xs text-danger">上次选择的模板已不可用，请重新选择。</p>}
          {templateError && <p role="alert" className="mt-2 text-sm text-danger">{templateError}</p>}
        </div>
        <p className="mt-5 text-xs leading-6 text-fg-muted">口播内容与媒体使用固定样本。选择模板后生成完整视频，当前选择和最近成片会自动保存。</p>
        <Button className="mt-6 w-full" disabled={!key || !selected || !!selected.validation_error || !!templateError || submitting || running} onClick={() => { void submit(); }}>
          {submitting || running ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{submitting ? "正在提交…" : running ? "正在合成…" : "开始合成"}
        </Button>
        {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
        {storageError && <p role="alert" className="mt-3 text-sm text-danger">{storageError}</p>}
      </section>
      <section aria-label="合成视频" className="min-w-0 overflow-hidden rounded-2xl border border-line bg-surface">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-6 py-4"><h2 className="font-semibold">合成视频</h2>
          <div className="flex items-center gap-3"><span role="status" className="text-xs text-fg-muted">{saved.task ? stages[saved.task.stage] || "处理中" : "等待首次合成"}</span>
            <Button variant="outline" size="sm" disabled={!key || !saved.task || querying} onClick={() => setQueryAttempt((value) => value + 1)}>{querying ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}{querying ? "查询中…" : "查询进度"}</Button></div></div>
        {saved.task && <div className="border-b border-line px-6 py-4">
          <p className="mb-2 text-xs text-fg-muted">{saved.task.status === "failed" ? "失败阶段" : "处理进度"} · {step} / {steps.length}</p>
          <div role="progressbar" aria-label="视频合成进度" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={step} aria-valuetext={`${saved.task.status === "failed" ? "失败于" : "当前"}${stages[steps[step - 1]]}`} className="h-1.5 overflow-hidden rounded-full bg-raised">
            <div className="h-full rounded-full bg-fg transition-[width]" style={{ width: `${step / steps.length * 100}%` }} />
          </div>
          <p className="mt-2 text-[11px] leading-5 text-fg-muted">按处理阶段显示，不代表耗时比例。{running && (queryError ? "查询已暂停，请点击查询进度重试。" : "每 15 秒自动查询，也可手动查询。")}</p>
        </div>}
        {saved.video ? <div className="p-5"><video key={saved.video.videoUrl} ref={video} aria-label="合成视频播放器" controls playsInline preload="metadata" src={saved.video.videoUrl} onError={() => setMediaError(true)} className="mx-auto max-h-[68dvh] w-full rounded-lg bg-canvas object-contain" />
          <p className="mt-3 truncate text-xs text-fg-muted">最近成片 · {saved.video.templateName} · {saved.video.durationSeconds.toFixed(1)} 秒</p>
          {mediaError && <p role="alert" className="mt-3 text-sm text-danger">视频暂时无法播放，请检查网络后<Button variant="link" size="sm" onClick={() => { setMediaError(false); video.current?.load(); }}>重试播放</Button></p>}
        </div> : <div className="flex min-h-[420px] flex-col items-center justify-center px-6 text-center text-fg-subtle"><Film className="mb-5 size-10 stroke-1" /><p className="text-sm text-fg-muted">{running ? `${stages[saved.task?.stage || ""] || "视频正在合成"}，完成后会显示在这里` : "选择模板，生成第一条视频"}</p><p className="mt-2 text-xs">支持播放、拖动进度与全屏查看</p></div>}
        {saved.task?.error && <p role="alert" className="border-t border-line px-6 py-4 text-sm text-danger">{saved.task.error.message}</p>}
        {queryError && <div role="alert" className="border-t border-line px-6 py-4 text-sm text-danger">暂时无法查询：{queryError.message}
          {queryError.missing && <Button variant="outline" size="sm" className="ml-3" disabled={querying} onClick={() => {
            setQueryError(null);
            void persist({ ...current.current, task: null, taskStyleId: "", taskTemplateName: "" });
          }}>清除失效任务</Button>}
        </div>}
      </section>
    </div>
  </div>;
}
