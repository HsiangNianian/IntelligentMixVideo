/** 主页按云端、本地顺序读取模板；各库独立显示状态，选择后交给模板工作区编辑。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Cloud, FolderOpen, Layers, Monitor, Plus, RefreshCw, Sparkles, SquarePen, WandSparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FlipText } from "@/components/ui/flip-text";
import { Hint } from "@/components/Hint";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { listTemplates, type Environment } from "./api";
import type { Template } from "./model";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/** 主页传递已有模板标识，或携带新模板的名称与描述；创建草稿时不写入存储。 */
export type TemplateSelection = { environment: Environment } & (
  | { templateId: string }
  | { templateId: null; name: string; description: string }
);

/** 主页只负责选择，编辑交给模板工作区。 */
interface Props {
  onSelect: (selection: TemplateSelection) => void;
}

/** 两个模板库分别读取和重试；卸载取消 HTTP，并忽略迟到的本地 IPC 结果。 */
function useTemplateCollection(environment: Environment) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const unavailable = environment === "local" && !isTauri();

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
  }, [environment, attempt, unavailable]);

  return { templates, loading, error, unavailable, refresh: () => setAttempt((value) => value + 1) };
}

/** 根据模板 ID 生成稳定色相，让每个模板封面可区分，同一模板每次显示相同颜色。 */
function coverHue(id: string): number {
  // FNV-1a 哈希后乘黄金角，相近 ID 也能分散到明显不同的色相。
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  return Math.round(hash * 137.508) % 360;
}

/** 封面模拟一帧成片：渐变背景上按模板自身的标题与字幕文字排版，没有文字对象时显示模板名称。 */
function TemplateCover({ template }: { template: Template }) {
  const hue = coverHue(template.template_id);
  const title = template.tracks.find((track) => track.target === "title")?.editor.title;
  const subtitle = template.tracks.find((track) => track.target === "subtitle")?.editor.subtitle;
  return (
    <span aria-hidden="true" className="relative flex aspect-video w-full flex-col items-center justify-between overflow-hidden rounded-lg px-3 py-3 text-center ring-1 ring-border transition duration-200 group-hover:ring-2 group-hover:ring-primary motion-reduce:transition-none"
      style={{ background: `radial-gradient(110% 100% at 15% 0%, oklch(0.62 0.14 ${hue}) 0%, transparent 62%), radial-gradient(90% 90% at 100% 100%, oklch(0.5 0.15 ${(hue + 50) % 360}) 0%, transparent 70%), oklch(0.27 0.06 ${hue})` }}>
      <span className="mt-3 line-clamp-2 text-[13px] font-bold leading-tight text-white [text-shadow:0_1px_6px_rgb(0_0_0/45%)]">{title || template.name}</span>
      {subtitle && <span className="line-clamp-1 rounded bg-black/45 px-1.5 py-0.5 text-[10px] text-white/90">{subtitle}</span>}
      <span className="absolute inset-0 flex items-center justify-center bg-black/45 opacity-0 transition-opacity group-hover:opacity-100 motion-reduce:transition-none">
        <span className="flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-lg"><SquarePen className="size-3.5" />打开编辑</span>
      </span>
      <span className="absolute right-2 top-2 flex items-center gap-1 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white/85 group-hover:opacity-0">
        <Layers className="size-3" />{template.tracks.length}
      </span>
    </span>
  );
}

/** 更新日期使用本地短格式，画廊只需要日期级别的新旧判断。 */
const updatedFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" });

/** 模板分组展示加载结果，整张卡片按钮传递模板 ID 和所属环境。 */
export function TemplateCollection({ environment, collection, onSelect }: Props & {
  environment: Environment;
  collection: ReturnType<typeof useTemplateCollection>;
}) {
  const headingId = useId();
  const { templates, loading, error, unavailable, refresh } = collection;
  const title = environment === "cloud" ? "云端模板" : "本地模板";
  const Icon = environment === "cloud" ? Cloud : FolderOpen;

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <Icon className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
          <h3 id={headingId} className="text-sm font-semibold">{title}</h3>
          {!loading && !error && !unavailable && (
            <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground" aria-label={`${templates.length} 个模板`}>{templates.length}</span>
          )}
          <span className="hidden truncate text-xs text-muted-foreground sm:inline">{environment === "cloud" ? "团队共享，保存在服务端" : "仅保存在本机"}</span>
        </div>
        {!unavailable && (
          <Hint label={`${error ? "重试" : "刷新"}${title}`}><Button variant="ghost" size="icon-sm" disabled={loading} onClick={refresh}
            aria-label={`${error ? "重试" : "刷新"}${title}`}>
            <RefreshCw className={cn("size-3.5 text-muted-foreground", loading && "animate-spin")} aria-hidden="true" />
          </Button></Hint>
        )}
      </div>
      {unavailable ? (
        <Empty className="border p-6 md:p-8">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Monitor className="size-5" strokeWidth={1.75} aria-hidden="true" /></EmptyMedia>
            <EmptyTitle className="text-sm">本地模板需要桌面客户端</EmptyTitle>
            <EmptyDescription className="text-xs">请在桌面客户端中查看和选择本地模板。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : loading ? (
        <div className={gridClass}>
          <p role="status" className="sr-only">正在读取{title}…</p>
          {[0, 1, 2].map((index) => <div key={index} aria-hidden="true" className="space-y-2.5"><Skeleton className="aspect-video rounded-lg" /><Skeleton className="h-3.5 w-2/3" /><Skeleton className="h-3 w-1/2" /></div>)}
        </div>
      ) : error ? (
        <p role="alert" className="break-words rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive [overflow-wrap:anywhere]">{error}</p>
      ) : templates.length === 0 ? (
        <Empty className="border p-6 md:p-8">
          <EmptyHeader>
            <EmptyMedia variant="icon"><Sparkles className="size-5" strokeWidth={1.75} aria-hidden="true" /></EmptyMedia>
            <EmptyTitle className="text-sm">暂无{title}</EmptyTitle>
            <EmptyDescription className="text-xs">点击上方「新建{title}」开始创作。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className={gridClass}>
          {templates.map((template) => (
            <li key={template.template_id} className="min-w-0">
              <button
                type="button"
                className="group flex w-full cursor-pointer flex-col gap-2.5 rounded-lg text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                aria-label={`选择模板：${template.name}`}
                onClick={() => onSelect({ environment, templateId: template.template_id })}
              >
                <TemplateCover template={template} />
                <span className="min-w-0 px-0.5">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium transition-colors group-hover:text-primary" title={template.name}>{template.name}</span>
                    <time dateTime={template.updated_at} className="shrink-0 font-mono text-[11px] text-muted-foreground">{updatedFormat.format(new Date(template.updated_at))}</time>
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">{template.description || "暂无模板说明"}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** 画廊按可用宽度自动排列，卡片保持最小可读宽度。 */
const gridClass = "grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-x-4 gap-y-6";

/** 首屏快捷入口：大号操作卡片，图标、标题与一句说明；禁用时说明原因。 */
function CreateTile({ icon: Icon, title, description, onClick, disabled, featured }: {
  icon: typeof Cloud;
  title: string;
  description: string;
  onClick: () => void;
  disabled?: boolean;
  featured?: boolean;
}) {
  return (
    <button type="button" aria-label={title} disabled={disabled} onClick={onClick} title={disabled ? description : undefined}
      className={cn("group relative flex min-w-0 cursor-pointer items-center gap-3.5 overflow-hidden rounded-xl border p-4 text-left outline-none transition hover:-translate-y-0.5 focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 motion-reduce:transition-none",
        featured ? "border-primary/25 bg-linear-to-br from-primary/12 to-transparent hover:border-primary/50" : "bg-card hover:border-foreground/20 hover:bg-accent/60")}>
      <span className={cn("flex size-11 shrink-0 items-center justify-center rounded-lg", featured ? "bg-primary text-primary-foreground shadow-sm" : "bg-muted text-foreground")}>
        <Icon className="size-5" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-1 text-sm font-semibold">{title}<Plus className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" /></span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{description}</span>
      </span>
    </button>
  );
}

/** 主页：顶部快捷创作入口，下方按云端、本地分组展示模板画廊；再次进入主页重新读取两库。 */
export function TemplateHome({ onSelect, onOpenRemotion }: Props & { onOpenRemotion: () => void }) {
  const cloud = useTemplateCollection("cloud");
  const local = useTemplateCollection("local");
  const [creating, setCreating] = useState<Environment | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState("");
  const formId = useId();
  const title = creating === "local" ? "本地模板" : "云端模板";

  /** 每次打开创建表单时清空上一次尚未提交的输入。 */
  function openCreation(environment: Environment) {
    setName("");
    setDescription("");
    setCreateError("");
    setCreating(environment);
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1400px] space-y-10 px-1 pb-10 pt-6 sm:pt-8">
      <section aria-label="开始创作" className="space-y-4">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-primary">Start creating</p>
          <h2 className="mt-1.5 text-2xl font-semibold tracking-tight"><FlipText loop={false} duration={1.6}>开始创作</FlipText></h2>
          <p className="mt-1 text-sm text-muted-foreground">新建模板设计文字与特效，或用对话生成可调参数的字效。</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <CreateTile featured icon={Cloud} title="新建云端模板" description="团队共享，保存在服务端" onClick={() => openCreation("cloud")} />
          <CreateTile icon={FolderOpen} title="新建本地模板" description={local.unavailable ? "本地模板需要桌面客户端" : "仅保存在本机，可离线编辑"} disabled={local.unavailable} onClick={() => openCreation("local")} />
          <CreateTile icon={WandSparkles} title="AI 生成字效" description="在 Remotion 字效中用对话生成" onClick={onOpenRemotion} />
        </div>
      </section>
      <section aria-labelledby={`${formId}-library`} className="space-y-6">
        <div className="flex items-end justify-between gap-3 border-b pb-3">
          <h1 id={`${formId}-library`} className="text-lg font-semibold tracking-tight">我的模板</h1>
          <p className="text-xs text-muted-foreground">选择模板继续编辑</p>
        </div>
        <TemplateCollection environment="cloud" collection={cloud} onSelect={onSelect} />
        <TemplateCollection environment="local" collection={local} onSelect={onSelect} />
      </section>
      <Dialog open={creating !== null} onOpenChange={(open) => { if (!open) setCreating(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建{title}</DialogTitle>
            <DialogDescription>填写模板信息后进入编辑页面，设置效果并点击保存。</DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={(event) => {
            event.preventDefault();
            if (creating === null) return;
            const trimmed = name.trim();
            if (!trimmed) { setCreateError("请输入模板名称"); return; }
            if ((creating === "cloud" ? cloud : local).templates.some((template) => template.name === trimmed)) {
              setCreateError("模板名称已存在，请使用其他名称");
              return;
            }
            onSelect({ environment: creating, templateId: null, name: trimmed, description: description.trim() });
            setCreating(null);
          }}>
            <div className="space-y-2">
              <Label htmlFor={`${formId}-name`}>模板名称</Label>
              <Input id={`${formId}-name`} required maxLength={100} value={name} onChange={(event) => { setName(event.target.value); setCreateError(""); }} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${formId}-description`}>模板描述</Label>
              <Textarea id={`${formId}-description`} maxLength={1000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述风格或适用场景（可选）" />
            </div>
            {createError && <p role="alert" className="text-sm text-destructive">{createError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreating(null)}>取消</Button>
              <Button type="submit">进入编辑</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
