/**
 * 云端模板网格：读取失败显示原因与重试，空列表提供新建入口。
 */
import type { ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import { Icon } from "../icons";
import { Button } from "../ui";
import type { TemplateSummary } from "../types";
import { TemplateCard, TemplateCardSkeleton } from "./TemplateCard";

export type SectionState = "ready" | "loading" | "error";

interface SectionProps {
  state: SectionState;
  templates: TemplateSummary[];
  error?: string;
  onRefresh: () => void;
  onCreate: () => void;
  editing: { id: string | null; dirty: boolean } | null;
  onOpen: (template: TemplateSummary) => void;
  onDelete: (template: TemplateSummary) => void;
  onCopyId: (template: TemplateSummary) => void;
}

/** 卡片网格按可用宽度自动排列，最小约 196px，窄屏自然变成两列或一列。 */
const grid = "grid grid-cols-[repeat(auto-fill,minmax(196px,1fr))] gap-x-4 gap-y-7";

/** 按读取状态显示错误说明、空列表或卡片网格。 */
export function TemplateSection({ state, templates, error, onRefresh, onCreate, editing, onOpen, onDelete, onCopyId }: SectionProps) {
  return (
    <section aria-label="模板列表" className="space-y-5">
      {state === "error" && (
        <Notice icon={<Icon name="cloud-cross" variant="duotone" />} title="云端模板读取失败" action={<Button size="sm" onClick={onRefresh}><Icon name="restart" />重试</Button>}>
          <span className="font-mono text-xs text-fg-muted">{error}</span>
        </Notice>
      )}

      {state === "ready" && templates.length === 0 && (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
          <EmptyFrames />
          <p className="mt-5 text-sm font-medium">还没有云端模板</p>
          <p className="mt-1 max-w-sm text-[13px] text-fg-muted">新建一个模板并保存后，团队里的每个人都能使用。</p>
          <Button size="sm" variant="secondary" className="mt-5" onClick={onCreate}><Icon name="add-circle" />新建云端模板</Button>
        </div>
      )}

      {(state === "loading" || templates.length > 0) && (
        <div className={grid}>
          <AnimatePresence>
            {state === "loading" ? [
              <span key="status" role="status" className="sr-only">正在读取云端模板</span>,
              ...Array.from({ length: 5 }, (_, index) => <TemplateCardSkeleton key={`loading-${index}`} />),
            ] : state === "ready" ? templates.map((template, index) => (
              <TemplateCard
                key={template.template_id}
                template={template}
                index={index}
                editing={template.template_id === editing?.id}
                dirty={editing?.dirty ?? false}
                onOpen={() => onOpen(template)}
                onDelete={() => onDelete(template)}
                onCopyId={() => onCopyId(template)}
              />
            )) : []}
          </AnimatePresence>
        </div>
      )}
    </section>
  );
}

/** 读取失败提示条：红色调，右侧放重试按钮。 */
function Notice({ icon, title, action, children }: { icon: ReactNode; title: string; action: ReactNode; children: ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-4 rounded-2xl border border-danger/25 bg-danger/[0.06] px-5 py-4">
      <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-danger/15 text-danger [&_svg]:size-[18px]">{icon}</span>
      <div className="min-w-0 flex-1 text-[13px] leading-relaxed text-fg-muted">
        <p className="text-sm font-medium text-fg">{title}</p>
        <div className="mt-0.5">{children}</div>
      </div>
      <div className="self-center">{action}</div>
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
