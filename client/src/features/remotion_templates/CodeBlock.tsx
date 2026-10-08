/**
 * 字效成功版本的只读代码视图：带行号的展示用高亮、诊断行标记与可跳转的诊断清单。
 *
 * 责任边界：只渲染已验收版本的代码与既有诊断结论，不编辑、不保存、不评估代码质量。
 * 高亮来自 `codeHighlight` 的近似分词；真实结论只用服务端隔离类型检查返回的诊断。
 *
 * 数据流：`code` 与 `diagnostics` 由 `VersionCard` 传入（diagnostics 可为空，表示尚未读取
 * 或读取失败，由 `notice` 区分），按行拆分后逐行着色，诊断按 1 基行号挂到对应行。
 */
import { useRef } from "react";
import { cn } from "@/lib/utils";
import { TOKEN_CLASS, highlight } from "./codeHighlight";
import type { Diagnostic } from "./model";

/** 行标记使用的严重级；只有 error 与 warning 会着色，其余仅进入清单。 */
type Marker = "error" | "warning" | null;

/** 取一行上最严重的诊断级别，用于行背景与无障碍名称。 */
function markerOf(items: Diagnostic[]): Marker {
  if (items.some((item) => item.severity === "error")) return "error";
  if (items.some((item) => item.severity === "warning")) return "warning";
  return null;
}

/** 诊断的 1 基行号；缺少范围时返回 null，表示无法定位到具体行。 */
function lineOf(item: Diagnostic): number | null {
  return item.range ? item.range.start.line + 1 : null;
}

/** 一行诊断的简短摘要：位置、级别、错误码与消息。 */
function summary(item: Diagnostic): string {
  const line = lineOf(item);
  const column = item.range ? item.range.start.character + 1 : null;
  return [
    line === null ? "文件级" : `第 ${line} 行${column === null ? "" : ` 第 ${column} 列`}`,
    item.severity,
    item.code ? `TS${item.code}` : item.source,
    item.message,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** 只读代码块；诊断清单点击后滚动到对应行，不改变版本选中状态。 */
export function CodeBlock({
  code,
  diagnostics,
  fileName,
  notice,
  onRetry,
}: {
  code: string;
  diagnostics: Diagnostic[];
  fileName: string;
  /** 诊断读取状态提示；为空表示无需提示。 */
  notice?: string;
  /** 诊断读取失败时的重试入口；省略表示不提供。 */
  onRetry?: () => void;
}) {
  const rows = useRef<Map<number, HTMLDivElement>>(new Map());
  const lines = code.split("\n");
  /** 诊断按 1 基行号分组，同一行的多条合并标记。 */
  const byLine = new Map<number, Diagnostic[]>();
  for (const item of diagnostics) {
    const line = lineOf(item);
    if (line === null || line < 1 || line > lines.length) continue;
    byLine.set(line, [...(byLine.get(line) ?? []), item]);
  }
  const located = diagnostics.filter((item) => lineOf(item) !== null);
  const unlocated = diagnostics.length - located.length;
  /** 滚动并聚焦目标行；不触发预览或版本选择。 */
  function reveal(line: number) {
    const row = rows.current.get(line);
    if (!row) return;
    row.scrollIntoView?.({ block: "nearest" });
    row.focus({ preventScroll: true });
  }
  return (
    <div className="border-t">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 font-mono text-[11px] text-muted-foreground">
        <span>{fileName}</span>
        {diagnostics.length > 0 && (
          <span
            role="status"
            className={cn(
              "rounded px-1.5 py-0.5 font-sans",
              diagnostics.some((item) => item.severity === "error")
                ? "bg-destructive/10 text-destructive"
                : "bg-amber-500/10 text-amber-700 dark:text-amber-400",
            )}
          >
            {diagnostics.length} 项诊断
          </span>
        )}
        {notice && (
          <span role="status" className="font-sans">
            {notice}
          </span>
        )}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="ml-auto font-sans text-primary underline-offset-2 hover:underline"
          >
            重试诊断
          </button>
        )}
      </div>
      <div
        role="group"
        aria-label="模板 TSX 代码"
        tabIndex={0}
        className="max-h-72 overflow-auto border-t bg-muted/30 py-3 font-mono text-xs leading-6"
      >
        {lines.map((text, position) => {
          const line = position + 1;
          const items = byLine.get(line) ?? [];
          const marker = markerOf(items);
          return (
            <div
              key={line}
              ref={(node) => {
                if (node) rows.current.set(line, node);
                else rows.current.delete(line);
              }}
              tabIndex={-1}
              data-line={line}
              aria-label={
                marker ? `第 ${line} 行：${marker}` : undefined
              }
              title={items.map(summary).join("\n") || undefined}
              className={cn(
                "flex gap-3 px-4 focus-visible:outline-2 focus-visible:outline-ring",
                marker === "error" && "bg-destructive/10",
                marker === "warning" && "bg-amber-500/10",
              )}
            >
              <span
                aria-hidden="true"
                className="w-8 shrink-0 select-none text-right text-muted-foreground/50"
              >
                {line}
              </span>
              <code
                data-code-line={line}
                className="min-w-0 whitespace-pre-wrap break-all"
              >
                {highlight(text).map((token, offset) => (
                  <span
                    key={offset}
                    className={TOKEN_CLASS[token.kind] || undefined}
                  >
                    {token.text}
                  </span>
                ))}
                {text === "" ? " " : null}
              </code>
            </div>
          );
        })}
      </div>
      {diagnostics.length > 0 && (
        <ul
          aria-label="代码诊断"
          className="max-h-40 overflow-auto border-t px-4 py-2 text-xs"
        >
          {diagnostics.map((item, offset) => {
            const line = lineOf(item);
            return (
              <li key={offset} className="flex items-start gap-2 py-0.5">
                <span
                  className={cn(
                    "shrink-0 font-medium",
                    item.severity === "error"
                      ? "text-destructive"
                      : item.severity === "warning"
                        ? "text-amber-700 dark:text-amber-400"
                        : "text-muted-foreground",
                  )}
                >
                  {item.severity}
                </span>
                {line === null ? (
                  <span className="min-w-0 break-words text-muted-foreground">
                    {summary(item)}
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => reveal(line)}
                    className="min-w-0 break-words text-left hover:underline"
                  >
                    {summary(item)}
                  </button>
                )}
              </li>
            );
          })}
          {unlocated > 0 && (
            <li className="py-0.5 text-muted-foreground">
              另有 {unlocated} 条无位置信息的诊断。
            </li>
          )}
        </ul>
      )}
      <p className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        高亮仅用于阅读，不参与版本验收；诊断来自服务端隔离类型检查。
      </p>
    </div>
  );
}
