/** 主页按云端、本地顺序读取模板；各库独立显示状态，选择后交给模板工作区编辑。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { ChevronRight, Clapperboard, Cloud, FolderOpen, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { listTemplates, type Environment } from "./api";
import type { Template } from "./model";

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

/** 模板分组展示加载结果，整行按钮传递模板 ID 和所属环境。 */
export function TemplateCollection({ environment, collection, onSelect }: Props & {
  environment: Environment;
  collection: ReturnType<typeof useTemplateCollection>;
}) {
  const headingId = useId();
  const { templates, loading, error, unavailable, refresh } = collection;
  const title = environment === "cloud" ? "云端模板" : "本地模板";
  const Icon = environment === "cloud" ? Cloud : FolderOpen;

  return (
    <section aria-labelledby={headingId}>
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-dashed bg-card/90 px-4 py-2.5 backdrop-blur sm:px-5">
        <div className="flex min-w-0 items-baseline gap-2.5">
          <h3 id={headingId} className="flex items-center gap-1.5 text-sm font-semibold">
            <Icon className="size-4 self-center text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />{title}
          </h3>
          <span className="hidden truncate text-xs text-muted-foreground sm:inline">{environment === "cloud" ? "团队共享，保存在服务端" : "仅保存在本机"}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {!loading && !error && !unavailable && (
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground" aria-label={`${templates.length} 个模板`}>{templates.length}</span>
          )}
          {!unavailable && (
            <Button variant="ghost" size="icon-sm" disabled={loading} onClick={refresh}
              aria-label={`${error ? "重试" : "刷新"}${title}`} title={`${error ? "重试" : "刷新"}${title}`}>
              <RefreshCw className="size-3.5 text-muted-foreground" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
      {unavailable ? (
        <div className="m-3 flex items-center gap-3 rounded-lg border border-dashed px-4 py-5 text-sm text-muted-foreground">
          <FolderOpen className="size-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <p>请在桌面客户端中查看和选择本地模板。</p>
        </div>
      ) : loading ? (
        <p role="status" className="px-4 py-6 font-mono text-xs text-muted-foreground sm:px-5">正在读取{title}…</p>
      ) : error ? (
        <p role="alert" className="m-3 break-words rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive [overflow-wrap:anywhere]">{error}</p>
      ) : templates.length === 0 ? (
        <div className="m-3 flex items-center gap-3 rounded-lg border border-dashed px-4 py-5 text-sm text-muted-foreground">
          <Sparkles className="size-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <p>暂无{title}，点击下方「新建{title}」开始创作。</p>
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-x-4 gap-y-5 p-4 min-[420px]:grid-cols-2 sm:p-5">
          {templates.map((template) => (
            <li key={template.template_id} className="min-w-0">
              {/* 画廊式卡片：灰底点阵画框作为缩略图，名称与说明位于画框下方。 */}
              <button
                type="button"
                className="group flex w-full cursor-pointer flex-col gap-2.5 rounded-xl text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                aria-label={`选择模板：${template.name}`}
                onClick={() => onSelect({ environment, templateId: template.template_id })}
              >
                <span aria-hidden="true" className="flex aspect-video w-full items-center justify-center rounded-xl border bg-muted/60 bg-[radial-gradient(var(--border)_1px,transparent_1px)] bg-[size:14px_14px] transition group-hover:-translate-y-0.5 group-hover:border-foreground/25 group-hover:shadow-md motion-reduce:transition-none">
                  <span className="flex size-10 items-center justify-center rounded-xl bg-foreground text-background shadow-sm transition-transform group-hover:scale-110 motion-reduce:transition-none">
                    <Clapperboard className="size-5" strokeWidth={1.75} />
                  </span>
                </span>
                <span className="min-w-0 px-0.5">
                  <span className="flex items-center gap-1 text-sm font-medium">
                    <span className="truncate" title={template.name}>{template.name}</span>
                    <ChevronRight className="size-3.5 shrink-0 -translate-x-1 text-muted-foreground opacity-0 transition group-hover:translate-x-0 group-hover:opacity-100 motion-reduce:transition-none" aria-hidden="true" />
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

/** 居中列表内部滚动，卡片底部操作栏保留两个新建入口；再次进入主页重新读取两库。 */
export function TemplateHome({ onSelect }: Props) {
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
    <div className="mx-auto flex h-full min-h-0 w-full min-w-0 max-w-xl flex-col gap-5 py-3 sm:py-5">
      <Card className="min-h-0 flex-1 gap-0 overflow-hidden py-0 shadow-none">
        <div role="region" aria-label="模板列表" tabIndex={0}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <TemplateCollection environment="cloud" collection={cloud} onSelect={onSelect} />
          <TemplateCollection environment="local" collection={local} onSelect={onSelect} />
        </div>
        {/* 卡片底部操作栏：新建入口与列表同属一张卡片，虚线与滚动区域分隔。 */}
        <div className="flex shrink-0 items-center justify-end gap-3 border-t border-dashed bg-muted/40 px-4 py-2.5 sm:px-5">
          <div className="flex min-w-0 gap-2">
            <Button className="h-8 min-w-0 cursor-pointer gap-1.5 rounded-md bg-foreground px-3 text-xs text-background shadow-sm hover:bg-foreground/85 sm:text-[13px]" onClick={() => openCreation("cloud")}>
              <Cloud strokeWidth={1.75} aria-hidden="true" />新建云端模板
            </Button>
            <Button variant="outline" className="h-8 min-w-0 cursor-pointer gap-1.5 rounded-md bg-card px-3 text-xs text-foreground shadow-sm hover:bg-muted disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-45 sm:text-[13px]"
              disabled={local.unavailable} title={local.unavailable ? "本地模板需要桌面客户端" : undefined}
              onClick={() => openCreation("local")}>
              <FolderOpen strokeWidth={1.75} aria-hidden="true" />新建本地模板
            </Button>
          </div>
        </div>
      </Card>
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
