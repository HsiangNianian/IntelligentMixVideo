/** 主页按云端、本地顺序读取模板；各库独立显示状态，选择后交给模板工作区编辑。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { ChevronRight, Cloud, FolderOpen, Plus, RefreshCw } from "lucide-react";
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

/** 主页只负责选择，模板详情读取和未保存保护由编辑工作区处理。 */
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
    <section aria-labelledby={headingId} className="border-b last:border-b-0">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b bg-card px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2">
          <Icon className="size-5 text-primary" aria-hidden="true" />
          <h3 id={headingId} className="font-semibold">{title}</h3>
          {!loading && !error && !unavailable && (
            <span className="rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-primary" aria-label={`${templates.length} 个模板`}>{templates.length}</span>
          )}
        </div>
        {!unavailable && (
          <Button variant="ghost" size="icon-sm" disabled={loading} onClick={refresh}
            aria-label={`${error ? "重试" : "刷新"}${title}`} title={`${error ? "重试" : "刷新"}${title}`}>
            <RefreshCw className="size-4 text-muted-foreground" aria-hidden="true" />
          </Button>
        )}
      </div>
      {unavailable ? (
        <p className="px-4 py-8 text-sm text-muted-foreground sm:px-6">请在桌面客户端中查看和选择本地模板。</p>
      ) : loading ? (
        <p role="status" className="px-4 py-8 text-sm text-muted-foreground sm:px-6">正在读取{title}…</p>
      ) : error ? (
        <p role="alert" className="m-3 break-words rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive [overflow-wrap:anywhere]">{error}</p>
      ) : templates.length === 0 ? (
        <p className="px-4 py-8 text-sm text-muted-foreground sm:px-6">暂无{title}，点击下方「新建{title}」开始创作。</p>
      ) : (
        <ul className="px-2">
          {templates.map((template) => (
            <li key={template.template_id} className="border-b last:border-b-0">
              <Button
                variant="ghost"
                className="my-1 h-auto min-h-20 w-full justify-between gap-4 whitespace-normal rounded-lg px-2 py-3 text-left hover:bg-accent/70 hover:text-primary focus-visible:ring-inset sm:px-4"
                aria-label={`选择模板：${template.name}`}
                onClick={() => onSelect({ environment, templateId: template.template_id })}
              >
                <span className="min-w-0 space-y-1">
                  <span className="block truncate text-base font-medium" title={template.name}>{template.name}</span>
                  <span className="line-clamp-2 break-words text-sm font-normal text-muted-foreground [overflow-wrap:anywhere]">{template.description || "暂无模板说明"}</span>
                </span>
                <ChevronRight className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** 居中列表内部滚动，底部保留两个新建入口；再次进入主页重新读取两库。 */
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
      <div className="shrink-0 space-y-2 text-center">
        <h2 className="text-2xl font-semibold tracking-tight">我的模板</h2>
        <p className="text-sm text-muted-foreground">选择模板，进入模版编辑页面继续创作。</p>
      </div>
      <Card className="min-h-0 flex-1 gap-0 overflow-hidden py-0">
        <div role="region" aria-label="模板列表" tabIndex={0}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
          <TemplateCollection environment="cloud" collection={cloud} onSelect={onSelect} />
          <TemplateCollection environment="local" collection={local} onSelect={onSelect} />
        </div>
      </Card>
      <div className="mx-auto grid w-full max-w-xs shrink-0 grid-cols-2 gap-3">
        <Button className="h-9 min-w-0 gap-1.5 rounded-lg px-2 text-xs sm:px-3 sm:text-sm" onClick={() => openCreation("cloud")}>
          <Plus aria-hidden="true" />新建云端模板
        </Button>
        <Button variant="outline" className="h-9 min-w-0 gap-1.5 rounded-lg border-primary/40 px-2 text-xs text-primary sm:px-3 sm:text-sm"
          disabled={local.unavailable} title={local.unavailable ? "本地模板需要桌面客户端" : undefined}
          onClick={() => openCreation("local")}>
          <Plus aria-hidden="true" />新建本地模板
        </Button>
      </div>
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
