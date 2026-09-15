/** 顶部只读代码浮板；复制展示的已验收导出，异步剪贴板失败有明确反馈。 */
import { cn } from "@/lib/utils";
import { useEffect, useState } from "react";
import { Check, ChevronDown, Code2, Copy, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 展开操作保留紧凑布局，新增由父级打开空白会话，后台任务保留在历史中。 */
export function CodePanel({
  code,
  pending,
  onNew,
}: {
  code: string;
  pending: boolean;
  onNew: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => setNotice(""), [code]);
  /** 复制成功版本的完整代码；浏览器拒绝访问时保留手动复制入口。 */
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setNotice("已复制");
    } catch {
      setNotice("复制失败，请展开代码后手动选择复制。");
    }
  }
  return (
    <section
      aria-label="Remotion 代码"
      className="rounded-2xl border bg-card shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 rounded-md text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring"
        >
          <Code2 className="size-4 text-muted-foreground" />
          Template.tsx
          <ChevronDown
            className={cn(
              "size-3 transition-transform",
              expanded ? "rotate-180" : "",
            )}
          />
        </button>
        <div className="flex items-center gap-2">
          <span
            role="status"
            className="max-w-60 text-xs text-muted-foreground"
          >
            {notice || (pending ? "调整确认后可复制" : "")}
          </span>
          <Button variant="outline" size="sm" onClick={onNew}>
            <Plus />
            新增
          </Button>
          <Button
            size="sm"
            disabled={!code || pending}
            onClick={() => void copy()}
          >
            {notice === "已复制" ? <Check /> : <Copy />}复制代码
          </Button>
        </div>
      </div>
      <pre
        aria-label="模板 TSX 代码"
        tabIndex={0}
        className={cn(
          "overflow-auto border-t bg-muted/30 px-5 py-3 font-mono text-xs leading-5 text-muted-foreground",
          expanded ? "max-h-72" : "max-h-24",
        )}
      >
        <code>
          {code ||
            "// 在左侧描述你想要的字效\n// 模板完成后，这里会显示可复制的 Remotion 代码。"}
        </code>
      </pre>
    </section>
  );
}
