/**
 * 当前页签的模板网格：两库卡片合并排列，各库读取失败仍可独立重试，不影响另一库。
 */
import type { ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import { Icon } from "../icons";
import { cn } from "@/lib/utils";
import { Button } from "../ui";
import type { Library, TemplateSummary } from "../types";
import { TemplateCard, TemplateCardSkeleton } from "./TemplateCard";

export type SectionState = "ready" | "loading" | "error" | "unavailable";

/** 两个库的固定文案。 */
const copy: Record<Library, { title: string; empty: string; emptyHint: string }> = {
  cloud: { title: "云端模板", empty: "还没有云端模板", emptyHint: "新建一个模板并保存后，团队里的每个人都能使用。" },
  local: { title: "本地模板", empty: "还没有本地模板", emptyHint: "本地模板不需要连接服务，适合先在自己电脑上试做。" },
};

interface TemplateLibrary {
  library: Library;
  state: SectionState;
  templates: TemplateSummary[];
  error?: string;
  onRefresh: () => void;
  onCreate: () => void;
}

interface SectionProps {
  libraries: TemplateLibrary[];
  editing: { library: Library; id: string | null; dirty: boolean } | null;
  onOpen: (library: Library, template: TemplateSummary) => void;
  onDelete: (library: Library, template: TemplateSummary) => void;
  onCopyId: (template: TemplateSummary) => void;
}

/** 卡片网格按可用宽度自动排列，最小约 196px，窄屏自然变成两列或一列。 */
const grid = "grid grid-cols-[repeat(auto-fill,minmax(196px,1fr))] gap-x-4 gap-y-7";

/** 仅由所选库生成卡片，空列表和错误说明不重复占用分组标题。 */
export function TemplateSection({ libraries, editing, onOpen, onDelete, onCopyId }: SectionProps) {
  const empty = libraries.every(({ state, templates }) => state === "ready" && templates.length === 0);
  const first = libraries[0];
  return (
    <section aria-label="模板列表" className="space-y-5">
      {libraries.map(({ library, state, error, onRefresh }) => state === "error" ? (
        <Notice key={library} tone="danger" icon={<Icon name={library === "cloud" ? "cloud-cross" : "laptop-minimalistic"} variant="duotone" />} title={`${copy[library].title}读取失败`} action={<Button size="sm" onClick={onRefresh}><Icon name="restart" />重试</Button>}>
          <span className="font-mono text-xs text-fg-muted">{error}</span>
        </Notice>
      ) : state === "unavailable" ? (
        <Notice key={library} icon={<Icon name="monitor" variant="duotone" />} title="本地模板需要桌面客户端">
          浏览器无法读取电脑上的文件，请在桌面客户端中查看和新建本地模板。
        </Notice>
      ) : null)}

      {empty && first && (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
          <EmptyFrames />
          <p className="mt-5 text-sm font-medium">{libraries.length === 1 ? copy[first.library].empty : "还没有模板"}</p>
          <p className="mt-1 max-w-sm text-[13px] text-fg-muted">{libraries.length === 1 ? copy[first.library].emptyHint : "从空白开始，创建你的第一个模板。"}</p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">{libraries.map(({ library, onCreate }) => (
            <Button key={library} size="sm" variant="secondary" onClick={onCreate}><Icon name="add-circle" />新建{copy[library].title}</Button>
          ))}</div>
        </div>
      )}

      {!empty && (
        <div className={grid}>
          <AnimatePresence>
            {libraries.flatMap(({ library, state, templates }) => state === "loading" ? [
              <span key={`${library}-status`} role="status" className="sr-only">正在读取{copy[library].title}</span>,
              ...Array.from({ length: library === "cloud" ? 5 : 2 }, (_, index) => <TemplateCardSkeleton key={`${library}-loading-${index}`} />),
            ] : state === "ready" ? templates.map((template, index) => (
              <TemplateCard
                key={`${library}-${template.template_id}`}
                library={library}
                template={template}
                index={index}
                editing={editing?.library === library && template.template_id === editing.id}
                dirty={editing?.dirty ?? false}
                onOpen={() => onOpen(library, template)}
                onDelete={() => onDelete(library, template)}
                onCopyId={() => onCopyId(template)}
              />
            )) : [])}
          </AnimatePresence>
        </div>
      )}
    </section>
  );
}

/** 横向提示条：失败用红色调，其他说明用中性色；右侧可放一个操作按钮。 */
function Notice({ tone = "neutral", icon, title, action, children }: { tone?: "neutral" | "danger"; icon: ReactNode; title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div role={tone === "danger" ? "alert" : undefined} className={cn("flex items-start gap-4 rounded-2xl border px-5 py-4", tone === "danger" ? "border-danger/25 bg-danger/[0.06]" : "border-line bg-surface")}>
      <span className={cn("mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl [&_svg]:size-[18px]", tone === "danger" ? "bg-danger/15 text-danger" : "bg-raised text-fg-muted")}>{icon}</span>
      <div className="min-w-0 flex-1 text-[13px] leading-relaxed text-fg-muted">
        <p className="text-sm font-medium text-fg">{title}</p>
        <div className="mt-0.5">{children}</div>
      </div>
      {action && <div className="self-center">{action}</div>}
    </div>
  );
}

/** 空列表插画：三张错落的空白竖屏画框。 */
function EmptyFrames() {
  return (
    <span aria-hidden="true" className="relative flex h-16 w-24 items-end justify-center">
      <span className="absolute h-14 w-10 -translate-x-6 -rotate-[10deg] rounded-md border border-line-strong bg-surface" />
      <span className="absolute h-14 w-10 translate-x-6 rotate-[10deg] rounded-md border border-line-strong bg-surface" />
      <span className="relative flex h-16 w-11 items-center justify-center rounded-md border border-line-strong bg-raised text-fg-subtle">
        <Icon name="add-circle" className="size-4" />
      </span>
    </span>
  );
}
