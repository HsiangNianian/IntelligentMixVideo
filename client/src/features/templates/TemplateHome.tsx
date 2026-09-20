/** 主页按云端、本地顺序读取模板；各库独立显示状态，选择后交给模板工作区编辑。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Cloud, FolderOpen, LayoutTemplate } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { listTemplates, type Environment } from "./api";
import type { Template } from "./model";

/** 环境和模板 ID 共同标识选择；同一个 ID 可以分别存在于两个模板库。 */
export interface TemplateSelection {
  environment: Environment;
  templateId: string;
}

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
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Icon className="size-5 text-primary" aria-hidden="true" />
          <h3 id={headingId} className="font-semibold">{title}</h3>
          {!loading && !error && !unavailable && (
            <span className="text-sm text-muted-foreground">{templates.length} 个</span>
          )}
        </div>
        {!unavailable && (
          <Button variant="outline" size="sm" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>
            {error ? "重试" : "刷新"}
          </Button>
        )}
      </div>
      {unavailable ? (
        <Card className="p-6 text-sm text-muted-foreground">请在桌面客户端中查看和选择本地模板。</Card>
      ) : loading ? (
        <p role="status" className="py-6 text-sm text-muted-foreground">正在读取{title}…</p>
      ) : error ? (
        <p role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">{error}</p>
      ) : templates.length === 0 ? (
        <Card className="p-6 text-sm text-muted-foreground">暂无{title}，可以前往模板库创建。</Card>
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
    </section>
  );
}

/** 两个库同时读取；重新进入主页时读取最新列表，包括编辑页保存或删除后的结果。 */
export function TemplateHome({ onSelect }: Props) {
  return (
    <div className="space-y-8 py-4">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">选择一个模板开始创作</h2>
        <p className="text-sm text-muted-foreground">选择模板后，可以在模板库中调整文字、动画和画面效果。</p>
      </div>
      <TemplateCollection environment="cloud" onSelect={onSelect} />
      <TemplateCollection environment="local" onSelect={onSelect} />
    </div>
  );
}
