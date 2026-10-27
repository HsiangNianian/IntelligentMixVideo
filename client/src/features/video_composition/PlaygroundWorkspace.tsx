/**
 * 操练场：用随包的固定样本与所选模板发起异步合成，保存当前状态，串行查询并播放最后成功的成片。
 * 界面分三栏：左栏选内容样本与模板并发起合成，中间按竖屏比例播放成片，右栏按真实阶段显示任务进度；
 * 窄窗口时任务栏落到画面下方，再窄则整页纵向排列。状态与请求逻辑集中在本组件，子组件只负责展示。
 */
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { apiBase } from "@/lib/api-base";
import { Icon } from "@/components/studio/icons";
import { Button, IconButton, Skeleton, Spinner } from "@/components/studio/ui";
import { TemplateCover } from "@/components/studio/home/TemplateCard";
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
/** 步骤列表里的名称：去掉「正在」，进行中由图标表示。 */
const stepLabels: Record<string, string> = {
  queued: "等待合成", assembling: "生成画面与字效", submitting: "提交云端合成", rendering: "IMS 渲染", uploading: "获取成片并转存 ZOS", completed: "合成完成",
};
/** 操练场受理时已固定模板，运行中报 template 阶段的只有 Remotion 资产与渲染环境检查，归入生成画面与字效。 */
const failureStages: Record<string, string> = { template: "assembling", remotion_render: "assembling", playback: "uploading", zos_upload: "uploading" };

/** 网络查询失败与任务失败分别显示，保留再次查询的机会。 */
function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/** 秒数写成「45 秒」「3 分 12 秒」或「2 小时 5 分」。 */
function duration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole} 秒`;
  if (whole < 3600) return `${Math.floor(whole / 60)} 分 ${whole % 60} 秒`;
  return `${Math.floor(whole / 3600)} 小时 ${Math.floor(whole / 60) % 60} 分`;
}

/** 复制文字并给出轻提示；剪贴板不可用时说明失败。 */
async function copy(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`已复制${label}`);
  } catch {
    toast.error("复制失败，请稍后重试");
  }
}

/** 离开页签暂停播放器；工作区保留挂载，合成查询和本地保存继续完成。 */
export function PlaygroundWorkspace({ active = true }: { active?: boolean }) {
  const address = apiBase();
  const [saved, setSaved] = useState<PlaygroundState>(emptyPlayground);
  const current = useRef(saved);
  const [key, setKey] = useState("");
  const currentKey = useRef("");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [templateError, setTemplateError] = useState("");
  const [error, setError] = useState("");
  const [storageError, setStorageError] = useState("");
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [resetting, setResetting] = useState(false);
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

  /** 仅重置已识别的损坏记录，写入成功后再恢复页面；切换服务后的旧结果不更新界面。 */
  async function resetStored() {
    const scope = currentKey.current;
    if (!scope || resetting) return;
    setResetting(true);
    try {
      await savePlayground(scope, emptyPlayground());
      if (mounted.current && currentKey.current === scope) setRestoreAttempt((value) => value + 1);
    } catch (reason) {
      if (mounted.current && currentKey.current === scope) setStorageError(`无法重置操练场：${message(reason)}`);
    } finally {
      if (mounted.current) setResetting(false);
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
    }).catch((reason) => {
      if (cancelled) return;
      if (reason instanceof Error && "key" in reason && typeof reason.key === "string") currentKey.current = reason.key;
      setStorageError(`无法恢复操练场：${message(reason)}`);
    });
    return () => { cancelled = true; };
  }, [address, restoreAttempt]);

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

  const pickDisabled = !key || loading || !!templateError || submitting || running;
  const canSubmit = !!key && !!selected && !selected.validation_error && !templateError && !submitting && !running;
  return <TooltipPrimitive.Provider delayDuration={350}>
    <div className="grid h-full min-h-0 overflow-y-auto bg-canvas lg:grid-cols-[340px_minmax(0,1fr)] lg:overflow-hidden xl:grid-cols-[340px_minmax(0,1fr)_320px]">
      <aside aria-label="合成设置" className="flex flex-col border-b lg:min-h-0 border-line bg-surface lg:border-b-0 lg:border-r">
        <header className="px-5 pb-4 pt-6">
          <p className="flex items-center gap-1.5 text-[11.5px] text-fg-subtle"><Icon name="clapperboard-edit" className="size-3.5" />视频合成</p>
          <h1 className="mt-1.5 text-xl font-semibold tracking-tight">操练场</h1>
          <p className="mt-1 text-[13px] text-fg-muted">同一份内容，换个模板看看效果。</p>
        </header>

        <fieldset className="px-5 pb-5">
          <legend className="mb-2 text-[12px] font-medium text-fg-muted">内容样本</legend>
          <div className="grid grid-cols-3 gap-1.5">
            <button type="button" aria-pressed="true" className="rounded-xl bg-brand/8 px-2 py-2.5 text-[13px] font-medium text-fg ring-1 ring-brand/45">IP口播<span className="mt-0.5 block text-[10.5px] font-normal text-brand">固定样本</span></button>
            {["情景展示", "新闻体"].map((label) => <button key={label} type="button" disabled className="rounded-xl px-2 py-2.5 text-[13px] text-fg-subtle ring-1 ring-line ring-inset">{label}<span className="mt-0.5 block text-[10.5px]">待初始化</span></button>)}
          </div>
          <p className="mt-2.5 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-fg-subtle"><Icon name="info-circle" className="mt-px size-3.5 shrink-0" />口播文案、配音与素材都用固定样本，只有模板不同。</p>
        </fieldset>

        <TemplatePicker templates={templates} loading={loading} error={templateError} value={saved.styleId} disabled={pickDisabled}
          onRefresh={() => setReload((value) => value + 1)} onChange={(styleId) => { void persist({ ...current.current, styleId }); }} />

        <footer className="space-y-3 border-t border-line p-4">
          {selected && <div className="flex items-center gap-2.5 rounded-xl bg-raised/70 p-2">
            <span className="w-8 shrink-0 overflow-hidden rounded-md ring-1 ring-line"><TemplateCover template={selected} compact /></span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">{selected.name}</span>
              <span className="block break-all font-mono text-[10.5px] text-fg-subtle">styleId {selected.template_id}</span>
            </span>
            <IconButton label="复制 styleId" className="size-7" onClick={() => { void copy(selected.template_id, " styleId"); }}><Icon name="copy" /></IconButton>
          </div>}
          <Button variant="primary" className="w-full" disabled={!canSubmit} onClick={() => { void submit(); }}>
            {submitting || running ? <Spinner /> : <Icon name="play" variant="duotone" />}{submitting ? "正在提交…" : running ? "正在合成…" : "开始合成"}
          </Button>
          {error && <Alert>{error}</Alert>}
          {storageError && <Alert>{storageError}</Alert>}
          {storageError && !key && <div className="space-y-2">
            {currentKey.current && <p className="text-[11.5px] text-fg-muted">重置将清除当前服务的模板选择、任务和最近成片记录。</p>}
            <div className="flex gap-2">
              <Button size="sm" disabled={resetting} onClick={() => setRestoreAttempt((value) => value + 1)}>重试读取</Button>
              {currentKey.current && <Button size="sm" disabled={resetting} onClick={() => { void resetStored(); }}>{resetting ? "正在重置…" : "重置操练场记录"}</Button>}
            </div>
          </div>}
        </footer>
      </aside>

      {/* 中等宽度时画面与任务上下排列、整列滚动；宽屏时这一层不生成盒子，两者各占一栏。 */}
      <div className="flex flex-col lg:min-h-0 lg:overflow-y-auto xl:contents">
        <Stage saved={saved} busy={running || submitting} videoRef={video} mediaError={mediaError}
          onMediaError={() => setMediaError(true)} onRetryMedia={() => { setMediaError(false); video.current?.load(); }} />
        <TaskPanel task={saved.task} templateName={saved.taskTemplateName} running={running} step={step} hasKey={!!key}
          querying={querying} queryError={queryError} onQuery={() => setQueryAttempt((value) => value + 1)}
          onClear={() => { setQueryError(null); void persist({ ...current.current, task: null, taskStyleId: "", taskTemplateName: "" }); }} />
      </div>
    </div>
  </TooltipPrimitive.Provider>;
}

/** 红色提示行，用于提交失败、本地记录与查询失败。 */
function Alert({ children }: { children: ReactNode }) {
  return <div role="alert" className="flex items-start gap-1.5 rounded-lg bg-danger/8 px-2.5 py-2 text-[12.5px] leading-relaxed text-danger"><Icon name="danger-circle" className="mt-0.5 size-3.5 shrink-0" /><div className="min-w-0">{children}</div></div>;
}

/**
 * 模板列表：可按名称或 ID 搜索的单选列表，每行有封面缩略图、名称与 ID 前 8 位；配置无效的模板不可选。
 * 用原生单选框承载选择与方向键切换，选中即保存。
 */
function TemplatePicker({ templates, loading, error, value, disabled, onRefresh, onChange }: {
  templates: TemplateSummary[]; loading: boolean; error: string; value: string; disabled: boolean;
  onRefresh: () => void; onChange: (styleId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const keyword = query.trim().toLowerCase();
  const shown = keyword ? templates.filter((template) => `${template.name} ${template.template_id}`.toLowerCase().includes(keyword)) : templates;
  const stale = !loading && !error && !!value && !templates.some((template) => template.template_id === value);
  return <section aria-label="模板" className="flex min-h-[280px] flex-1 flex-col border-t border-line lg:min-h-0">
    <div className="flex items-center gap-2 px-5 pb-2 pt-4">
      <h2 className="text-[12px] font-medium text-fg-muted">模板</h2>
      {!loading && !error && <span className="rounded-full bg-raised px-1.5 font-mono text-[10.5px] leading-[18px] text-fg-muted">{templates.length}</span>}
      <IconButton label="刷新模板" className="ml-auto size-7" disabled={loading} onClick={onRefresh}><Icon name="restart" className={cn(loading && "animate-spin")} /></IconButton>
    </div>
    <label className="mx-5 mb-2 flex h-8 items-center gap-2 rounded-lg border border-line bg-sunken px-2.5 focus-within:border-brand/50 focus-within:ring-[3px] focus-within:ring-brand/12">
      <Icon name="magnifier" className="size-3.5 shrink-0 text-fg-subtle" />
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或 styleId" aria-label="搜索模板" className="min-w-0 flex-1 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-fg-subtle" />
    </label>
    {stale && <div className="mx-5 mb-2"><Alert>上次选择的模板已不可用，请重新选择。</Alert></div>}
    <div className="max-h-[360px] min-h-0 flex-1 overflow-y-auto px-3 pb-3 lg:max-h-none">
      {loading ? <div aria-hidden="true" className="space-y-1 px-2 pt-1">{Array.from({ length: 5 }, (_, index) => <div key={index} className="flex items-center gap-3 py-1.5"><Skeleton className="h-10 w-8 rounded-md" /><span className="flex-1 space-y-1.5"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-2.5 w-1/3" /></span></div>)}</div>
        : error ? <div className="px-2 pt-1"><Alert>{error}</Alert></div>
        : !templates.length ? <p className="px-2 pt-2 text-[12.5px] leading-relaxed text-fg-muted">还没有已保存的模板，请先在模板编辑中保存。</p>
        : !shown.length ? <p className="px-2 pt-2 text-[12.5px] text-fg-muted">没有找到「{query.trim()}」相关的模板。</p>
        : <div role="radiogroup" aria-label="选择模板" className="space-y-0.5">
          {shown.map((template) => {
            const invalid = !!template.validation_error;
            const checked = template.template_id === value;
            return <label key={template.template_id} className={cn("group flex items-center gap-3 rounded-xl px-2 py-1.5 transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-brand",
              checked ? "bg-brand/8 ring-1 ring-inset ring-brand/40" : "hover:bg-raised/70", (disabled || invalid) ? "cursor-not-allowed" : "cursor-pointer", invalid && "opacity-60")}>
              <input type="radio" name="playground-template" className="sr-only" checked={checked} disabled={disabled || invalid} onChange={() => onChange(template.template_id)} />
              <span className="w-8 shrink-0 overflow-hidden rounded-md ring-1 ring-line">
                {invalid ? <span className="flex aspect-[4/5] items-center justify-center bg-raised text-warning"><Icon name="danger-triangle" className="size-3.5" /></span> : <TemplateCover template={template} compact />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{template.name}</span>
                <span className="block truncate text-[10.5px] text-fg-subtle">{invalid ? "配置无效，不能合成" : <span className="font-mono">{template.template_id.slice(0, 8)}</span>}</span>
              </span>
              {checked && <Icon name="check-circle" variant="duotone" className="size-4 shrink-0 text-brand" />}
            </label>;
          })}
        </div>}
    </div>
  </section>;
}

/**
 * 画面：按竖屏比例在可用空间里放播放器，宽高都不超出。`busy` 表示新任务正在提交或合成：只有成功的任务才保存成片，
 * 所以这时画面上的成片一定来自更早的任务，标为「上一条成片」；新任务结束后（失败也一样）它就是最新可用的成片。
 * 没有成片时显示同比例的虚线空画框。
 */
function Stage({ saved, busy, videoRef, mediaError, onMediaError, onRetryMedia }: {
  saved: PlaygroundState; busy: boolean; videoRef: RefObject<HTMLVideoElement | null>; mediaError: boolean;
  onMediaError: () => void; onRetryMedia: () => void;
}) {
  const shown = saved.video;
  const previous = busy && !!shown;
  const frame = "relative w-[min(100cqw,calc(100cqh*9/16))] aspect-[9/16] overflow-hidden rounded-[14px]";
  return <section aria-label="合成视频" className="flex min-h-[560px] flex-1 flex-col xl:min-h-0">
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-surface px-5">
      {shown ? <>
        <span className={cn("shrink-0 rounded-md px-1.5 text-[11px] font-medium leading-5", previous ? "bg-raised text-fg-muted" : "bg-success/12 text-success")}>{previous ? "上一条成片" : "最新成片"}</span>
        <span className="truncate text-[13px] font-medium">{shown.templateName}</span>
        <span className="shrink-0 text-[11.5px] tabular-nums text-fg-subtle">{duration(shown.durationSeconds)}</span>
        {previous && <span className="ml-auto flex shrink-0 items-center gap-1.5 text-[11.5px] text-fg-muted"><Spinner className="size-3 text-brand" />新视频合成中，完成后替换</span>}
      </> : <span className="text-[13px] font-medium">合成视频</span>}
    </header>
    <div className="relative min-h-0 flex-1 bg-[radial-gradient(var(--color-line-strong)_1px,transparent_1px)] bg-size-[18px_18px]">
      <div className="absolute inset-6 flex items-center justify-center [container-type:size]">
        {shown ? <div className={cn(frame, "bg-black shadow-pop ring-1 ring-black/10")}>
          <video key={shown.videoUrl} ref={videoRef} aria-label="合成视频播放器" controls playsInline preload="metadata" src={shown.videoUrl} onError={onMediaError} className="size-full object-contain" />
          {mediaError && <div role="alert" className="absolute inset-x-3 bottom-14 rounded-xl bg-black/70 p-3 text-center text-[12.5px] text-white backdrop-blur-md">视频暂时无法播放，请检查网络后
            <button type="button" onClick={onRetryMedia} className="ml-1 font-medium underline underline-offset-2">重试播放</button></div>}
        </div> : <div className={cn(frame, "flex flex-col items-center justify-center border-[1.5px] border-dashed border-line-strong bg-surface/60 px-6 text-center")}>
          {busy ? <Spinner className="size-6 text-brand" /> : <Icon name="video-frame-play-vertical" className="size-7 text-fg-subtle" />}
          <p className="mt-3 text-[13px] font-medium">{busy ? "视频正在合成" : "还没有成片"}</p>
          <p className="mt-1 text-[11.5px] leading-relaxed text-fg-subtle">{busy ? "完成后会在这里播放" : "在左侧选择模板，生成第一条视频"}</p>
        </div>}
      </div>
    </div>
  </section>;
}

/** 合成中按秒刷新当前时间，用于显示已用时；结束或卸载时停止。 */
function useNow(ticking: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ticking]);
  return now;
}

/**
 * 任务栏：状态标签、任务信息与用时、按阶段分段的进度条和逐步列表；失败原因写在失败的那一步下面。
 * 底部是查询进度按钮与自动查询说明，任务已不存在时可清除。
 */
function TaskPanel({ task, templateName, running, step, hasKey, querying, queryError, onQuery, onClear }: {
  task: CompositionTask | null; templateName: string; running: boolean; step: number; hasKey: boolean; querying: boolean;
  queryError: { message: string; missing: boolean } | null; onQuery: () => void; onClear: () => void;
}) {
  const now = useNow(running);
  const failed = task?.status === "failed";
  const started = task?.createdAt ? Date.parse(task.createdAt) : NaN;
  const ended = running ? now : task?.updatedAt ? Date.parse(task.updatedAt) : NaN;
  const elapsed = Number.isFinite(started) && Number.isFinite(ended) ? (ended - started) / 1000 : null;
  const tone = !task ? "bg-raised text-fg-muted" : failed ? "bg-danger/10 text-danger" : running ? "bg-brand/10 text-brand" : "bg-success/12 text-success";
  return <section aria-label="合成任务" className="flex shrink-0 flex-col border-t border-line bg-surface xl:min-h-0 xl:border-l xl:border-t-0">
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-line px-5">
      <h2 className="text-[13px] font-medium">合成任务</h2>
      <span role="status" className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11.5px] font-medium", tone)}>{task ? stages[task.stage] || "处理中" : "等待首次合成"}</span>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      {task && <dl className="mb-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-fg-subtle">模板</dt><dd className="truncate">{templateName || "—"}</dd>
        <dt className="text-fg-subtle">{running ? "已用时" : "用时"}</dt><dd className="tabular-nums">{elapsed === null ? "—" : duration(elapsed)}</dd>
        <dt className="text-fg-subtle">任务</dt><dd className="truncate font-mono text-[11.5px] text-fg-muted" title={task.taskId}>{task.taskId}</dd>
      </dl>}
      <div role="progressbar" aria-label="视频合成进度" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={task ? step : 0}
        aria-valuetext={task ? `${failed ? "失败于" : "当前"}${stages[steps[step - 1]]}` : "尚未开始"} className="mb-4 flex gap-1">
        {steps.map((name, index) => <span key={name} className={cn("h-1 flex-1 rounded-full",
          !task || index + 1 > step ? "bg-raised" : index + 1 < step || task.status === "succeeded" ? "bg-success" : failed ? "bg-danger" : "bg-brand")} />)}
      </div>
      <ol className="space-y-0.5">
        {steps.map((name, index) => {
          const position = index + 1;
          const state = !task ? "pending" : position < step || task.status === "succeeded" ? "done" : position > step ? "pending" : failed ? "failed" : "active";
          return <li key={name} className="relative flex gap-3 pb-3 last:pb-0">
            {index < steps.length - 1 && <span aria-hidden="true" className={cn("absolute left-[9px] top-6 bottom-0 w-px", state === "done" ? "bg-success/40" : "bg-line-strong")} />}
            <span aria-hidden="true" className="relative mt-0.5 flex size-[19px] shrink-0 items-center justify-center">
              {state === "done" ? <Icon name="check-circle" variant="duotone" className="size-[19px] text-success" />
                : state === "active" ? <Spinner className="size-4 text-brand" />
                : state === "failed" ? <Icon name="danger-circle" variant="duotone" className="size-[19px] text-danger" />
                : <span className="size-2 rounded-full border-[1.5px] border-line-strong" />}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p className={cn("text-[13px]", state === "pending" ? "text-fg-subtle" : state === "failed" ? "font-medium text-danger" : state === "active" ? "font-medium text-fg" : "text-fg-muted")}>{stepLabels[name]}</p>
              {state === "failed" && task?.error && <p className="mt-1 text-[12.5px] leading-relaxed text-fg-muted">{task.error.message}</p>}
            </div>
          </li>;
        })}
      </ol>
      <p className="mt-4 text-[11px] leading-relaxed text-fg-subtle">按处理阶段显示，不代表耗时比例。{!task && "开始合成后，这里会显示每一步的进度。"}</p>
      {queryError && <div className="mt-3"><Alert>暂时无法查询：{queryError.message}
        {queryError.missing && <Button size="sm" className="mt-2" disabled={querying} onClick={onClear}>清除失效任务</Button>}
      </Alert></div>}
    </div>
    <footer className="flex shrink-0 items-center gap-3 border-t border-line px-5 py-3">
      <p className="flex min-w-0 flex-1 items-center gap-1.5 text-[11.5px] text-fg-subtle"><Icon name="clock-circle" className="size-3.5 shrink-0" />
        <span className="truncate">{!task ? "还没有任务" : running ? (queryError ? "查询已暂停，可手动重试" : "每 15 秒自动查询") : "任务已结束"}</span></p>
      <Button size="sm" disabled={!hasKey || !task || querying} onClick={onQuery}>{querying ? <Spinner /> : <Icon name="restart" />}{querying ? "查询中…" : "查询进度"}</Button>
    </footer>
  </section>;
}
