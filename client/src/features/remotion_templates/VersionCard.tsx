/** 聊天中的成功版本卡片：独立预览、按需读取默认参数导出，折叠和复制不会改变预览选择。 */
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Code2, Copy, Play, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { diagnostics as readDiagnostics, exported } from "./api";
import { CodeBlock } from "./CodeBlock";
import type { Diagnostic, Version } from "./model";
import { publishSprite } from "@/features/sprites/api";
import { publishChoice } from "./spriteChoice";

/** 每张卡片只拥有本版本的读取与提示，卸载取消请求，不展示未经确认的参数草稿。 */
export function VersionCard({
  version,
  code: acceptedCode,
  selected,
  latest,
  disabled,
  previewDisabled,
  onPreview,
}: {
  version: Version;
  code?: string;
  selected: boolean;
  latest: boolean;
  disabled: boolean;
  previewDisabled: boolean;
  onPreview: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(acceptedCode ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState("");
  const [items, setItems] = useState<Diagnostic[]>([]);
  const [diagnosticsNotice, setDiagnosticsNotice] = useState("");
  const [diagnosticsFailed, setDiagnosticsFailed] = useState(false);
  const request = useRef<Promise<string> | null>(null);
  const diagnosticRequest = useRef<Promise<void> | null>(null);
  const [loaded, setLoaded] = useState(false);
  const scope = useRef(new AbortController());
  const copyAllowed = useRef(!disabled);
  copyAllowed.current = !disabled;
  useEffect(() => {
    scope.current = new AbortController();
    return () => scope.current.abort();
  }, []);
  useEffect(() => {
    if (acceptedCode) setCode(acceptedCode);
  }, [acceptedCode]);
  /** 展开与复制共享一次读取；失败后清空请求，允许用户显式重试。 */
  async function load() {
    if (acceptedCode || code) return acceptedCode || code;
    if (request.current) return request.current;
    const signal = scope.current.signal;
    setLoading(true);
    setError("");
    request.current = exported(version.id, signal)
      .then((value) => {
        if (!signal.aborted) setCode(value);
        return value;
      })
      .catch((reason) => {
        if (!signal.aborted) setError("代码读取失败，请重试。");
        throw reason;
      })
      .finally(() => {
        request.current = null;
        if (!signal.aborted) setLoading(false);
      });
    return request.current;
  }
  /** 读取服务端隔离类型检查；失败只影响诊断区，代码与复制照常可用。 */
  async function loadDiagnostics() {
    if (disabled || loaded || diagnosticRequest.current) return;
    const signal = scope.current.signal;
    setDiagnosticsNotice("正在检查代码…");
    setDiagnosticsFailed(false);
    diagnosticRequest.current = readDiagnostics(version.id, signal)
      .then((report) => {
        if (signal.aborted) return;
        setItems(report.diagnostics);
        setLoaded(true);
        setDiagnosticsNotice(!report.passed && !report.diagnostics.length
          ? "代码检查未通过，服务端未提供定位信息。" : "");
      })
      .catch((reason) => {
        if (signal.aborted) return;
        setItems([]);
        setDiagnosticsFailed(true);
        setDiagnosticsNotice(
          reason instanceof Error
            ? `诊断读取失败：${reason.message} 代码仍可查看。`
            : "诊断读取失败，代码仍可查看。",
        );
      })
      .finally(() => {
        diagnosticRequest.current = null;
      });
    await diagnosticRequest.current;
  }
  /** 显式重试诊断；不重试代码读取，也不改变预览选择。 */
  function retryDiagnostics() {
    if (disabled || diagnosticRequest.current) return;
    void loadDiagnostics();
  }
  /** 展开时同时读取代码与诊断；两者互不阻塞。 */
  function expand(value: boolean) {
    setOpen(value);
    if (!value) return;
    void load().catch(() => {});
    void loadDiagnostics();
  }
  /** 一键保存为资产；重复保存同一版本由服务端返回原资产，失败显示原因且可重试。 */
  async function saveAsset() {
    if (publishing) return;
    setPublishing(true);
    setNotice("");
    setPublishError("");
    try {
      const asset = await publishSprite(version.id, publishChoice(version));
      if (!scope.current.signal.aborted) setNotice(`已保存到资产「${asset.name}」。`);
    } catch (reason) {
      if (!scope.current.signal.aborted) setPublishError(reason instanceof Error ? reason.message : "保存到资产失败，请重试");
    } finally {
      if (!scope.current.signal.aborted) setPublishing(false);
    }
  }
  /** 复制固定版本的 Export.tsx；剪贴板失败时展开相同代码供手动复制。 */
  async function copy() {
    if (disabled || loading) return;
    setNotice("");
    let value: string;
    try {
      value = await load();
    } catch {
      return;
    }
    if (scope.current.signal.aborted || !copyAllowed.current) return;
    try {
      await navigator.clipboard.writeText(value);
      if (!scope.current.signal.aborted) setNotice("已复制");
    } catch {
      if (!scope.current.signal.aborted) {
        setOpen(true);
        setNotice("复制失败，请展开代码后手动选择复制。");
      }
    }
  }
  return (
    <Collapsible
      open={open}
      onOpenChange={expand}
      className={cn(
        "min-w-0 overflow-hidden rounded-xl border bg-background transition-colors",
        selected ? "border-primary/40 ring-1 ring-primary/10" : "border-border",
      )}
      aria-label={`成功版本 V${version.number}`}
    >
      <button
        type="button"
        onClick={onPreview}
        disabled={previewDisabled}
        aria-label={`预览 V${version.number}`}
        aria-pressed={selected}
        className="flex w-full items-center gap-3 p-3.5 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/8 text-primary">
          <Code2 className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
            V{version.number} · {version.spec.name}
            {latest && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                最新
              </span>
            )}
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {version.source === "user_parameters" ? "参数调整" : "智能体生成"} ·{" "}
            {new Date(version.created_at).toLocaleString("zh-CN", {
              month: "numeric",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-primary">
          <Play className="size-3" />
          {selected ? "预览中" : "预览"}
        </span>
      </button>
      <div className="flex items-center justify-between border-t bg-muted/20 px-2 py-1">
        <CollapsibleTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`展开 V${version.number} 代码`}
          >
            <ChevronDown
              className={cn(
                "size-3.5 transition-transform",
                open && "rotate-180",
              )}
            />
            {open ? "收起代码" : "查看代码"}
          </Button>
        </CollapsibleTrigger>
        <div className="flex items-center">
          <Button variant="ghost" size="sm" disabled={disabled || publishing} onClick={() => void saveAsset()}>
            <Wand2 />
            {publishing ? "保存中…" : "保存到资产"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled || loading}
            onClick={() => void copy()}
          >
            {notice === "已复制" ? <Check /> : <Copy />}
            {loading ? "读取中…" : "复制代码"}
          </Button>
        </div>
      </div>
      {notice && (
        <p role="status" className="px-3 pb-2 text-xs text-muted-foreground">
          {notice}
        </p>
      )}
      {publishError && (
        <p role="alert" className="px-3 pb-2 text-xs text-destructive">{publishError}</p>
      )}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between px-3 pb-2 text-xs text-destructive"
        >
          {error}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void load().catch(() => {})}
          >
            重试代码
          </Button>
        </div>
      )}
      <CollapsibleContent>
        <div className="border-t px-4 py-2 font-mono text-[11px] text-muted-foreground">
          Export.tsx
        </div>
        {code || acceptedCode ? (
          <CodeBlock
            code={code || acceptedCode || ""}
            diagnostics={
              loaded ? items : []
            }
            fileName="Export.tsx"
            notice={
              loading
                ? "正在读取代码…"
                : diagnosticsNotice
            }
            onRetry={diagnosticsFailed && !disabled ? retryDiagnostics : undefined}
          />
        ) : (
          <pre
            aria-label="模板 TSX 代码"
            tabIndex={0}
            className="max-h-72 overflow-auto border-t bg-muted/30 p-4 font-mono text-xs leading-6"
          >
            <code>{loading ? "正在读取代码…" : "代码暂不可用"}</code>
          </pre>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
