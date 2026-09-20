/** 主页按云端、本地顺序读取模板；各库独立显示状态，选择后交给模板工作区编辑。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Cloud, FolderOpen, LayoutTemplate, Plus } from "lucide-react";
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

/** 每个模板库独立加载和重试；卸载取消 HTTP，并忽略迟到的本地 IPC 结果。 */
function TemplateCollection({ environment, onSelect }: Props & { environment: Environment }) {
  const headingId = useId();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState("");
  const unavailable = environment === "local" && !isTauri();
  const title = environment === "cloud" ? "云端模板" : "本地模板";
  const Icon = environment === "cloud" ? Cloud : FolderOpen;

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

  return (
    <section aria-labelledby={headingId} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="size-5 text-primary" aria-hidden="true" />
          <h3 id={headingId} className="font-semibold">{title}</h3>
          {!loading && !error && !unavailable && (
            <span className="text-sm text-muted-foreground">{templates.length} 个</span>
          )}
        </div>
        {!unavailable && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>
              {error ? "重试" : "刷新"}
            </Button>
            <Button size="sm" onClick={() => { setName(""); setDescription(""); setCreateError(""); setCreating(true); }}>
              <Plus aria-hidden="true" />新建模板
            </Button>
          </div>
        )}
      </div>
      {unavailable ? (
        <Card className="p-6 text-sm text-muted-foreground">请在桌面客户端中查看和选择本地模板。</Card>
      ) : loading ? (
        <p role="status" className="py-6 text-sm text-muted-foreground">正在读取{title}…</p>
      ) : error ? (
        <p role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">{error}</p>
      ) : templates.length === 0 ? (
        <Card className="p-6 text-sm text-muted-foreground">暂无{title}，点击「新建模板」开始创作。</Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {templates.map((template) => (
            <Card key={template.template_id} className="min-w-0 gap-4 p-5">
              <div className="flex items-start gap-3">
                <LayoutTemplate className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
                <h4 className="min-w-0 break-words font-medium">{template.name}</h4>
              </div>
              <p className="line-clamp-3 break-words text-sm text-muted-foreground">{template.description || "暂无模板说明"}</p>
              <Button
                variant="outline"
                className="mt-auto w-full"
                aria-label={`选择模板：${template.name}`}
                onClick={() => onSelect({ environment, templateId: template.template_id })}
              >
                选择模板
              </Button>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建{title}</DialogTitle>
            <DialogDescription>填写模板信息后进入编辑页面，设置效果并点击保存。</DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={(event) => {
            event.preventDefault();
            const trimmed = name.trim();
            if (!trimmed) { setCreateError("请输入模板名称"); return; }
            if (templates.some((template) => template.name === trimmed)) {
              setCreateError("模板名称已存在，请使用其他名称");
              return;
            }
            onSelect({ environment, templateId: null, name: trimmed, description: description.trim() });
            setCreating(false);
          }}>
            <div className="space-y-2">
              <Label htmlFor={`${headingId}-name`}>模板名称</Label>
              <Input id={`${headingId}-name`} required maxLength={100} value={name} onChange={(event) => { setName(event.target.value); setCreateError(""); }} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${headingId}-description`}>模板描述</Label>
              <Textarea id={`${headingId}-description`} maxLength={1000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述风格或适用场景（可选）" />
            </div>
            {createError && <p role="alert" className="text-sm text-destructive">{createError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreating(false)}>取消</Button>
              <Button type="submit">进入编辑</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** 两个库同时读取；重新进入主页时读取最新列表，包括编辑页保存后的结果。 */
export function TemplateHome({ onSelect }: Props) {
  return (
    <div className="space-y-8 py-4">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">选择一个模板开始创作</h2>
        <p className="text-sm text-muted-foreground">选择已有模板或新建模板，在模板库中调整文字、动画和画面效果后保存。</p>
      </div>
      <TemplateCollection environment="cloud" onSelect={onSelect} />
      <TemplateCollection environment="local" onSelect={onSelect} />
    </div>
  );
}
