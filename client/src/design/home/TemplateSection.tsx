/**
 * 主页的一个模板库分组（云端或本地）：标题行带存储说明、数量与刷新，正文按状态显示卡片网格、
 * 骨架屏、加载失败、空列表或「需要桌面客户端」。两个分组各自加载、各自重试，互不影响。
 */
import type { ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import { Icon } from "../icons";
import { cn } from "@/lib/utils";
import { Button, IconButton } from "../ui";
import type { Library, TemplateSummary } from "../mock";
import { TemplateCard, TemplateCardSkeleton } from "./TemplateCard";

export type SectionState = "ready" | "loading" | "error" | "unavailable";

/** 两个库的固定文案。 */
const copy: Record<Library, { title: string; hint: string; icon: ReactNode; empty: string; emptyHint: string }> = {
  cloud: { title: "云端模板", hint: "团队共享，保存在服务端", icon: <Icon name="cloud" />, empty: "还没有云端模板", emptyHint: "新建一个模板并保存后，团队里的每个人都能使用。" },
  local: { title: "本地模板", hint: "只保存在这台电脑上", icon: <Icon name="laptop-minimalistic" />, empty: "还没有本地模板", emptyHint: "本地模板不需要连接服务，适合先在自己电脑上试做。" },
};

interface SectionProps {
  library: Library;
  state: SectionState;
  templates: TemplateSummary[];
  error?: string;
  openId: string | null;
  dirty: boolean;
  onRefresh: () => void;
  onCreate: () => void;
  onOpen: (template: TemplateSummary) => void;
  onDelete: (template: TemplateSummary) => void;
  onCopyId: (template: TemplateSummary) => void;
}

/** 卡片网格按可用宽度自动排列，最小约 196px，窄屏自然变成两列或一列。 */
const grid = "grid grid-cols-[repeat(auto-fill,minmax(196px,1fr))] gap-x-4 gap-y-7";

/** 一个模板库分组。 */
export function TemplateSection({ library, state, templates, error, openId, dirty, onRefresh, onCreate, onOpen, onDelete, onCopyId }: SectionProps) {
  const text = copy[library];
  const loading = state === "loading";
  return (
    <section aria-labelledby={`section-${library}`} className="space-y-5">
      <header className="flex items-center gap-3">
        <span className="flex size-7 items-center justify-center rounded-lg bg-raised text-fg-muted [&_svg]:size-[15px]">{text.icon}</span>
        <h3 id={`section-${library}`} className="text-[15px] font-semibold tracking-tight">{text.title}</h3>
        {state === "ready" && (
          <span className="rounded-full bg-raised px-2 py-px font-mono text-[11px] tabular-nums text-fg-muted">{templates.length}</span>
        )}
        <span className="hidden truncate text-xs text-fg-subtle sm:inline">{text.hint}</span>
        {state !== "unavailable" && (
          <IconButton label={`刷新${text.title}`} className="ml-auto" disabled={loading} onClick={onRefresh}>
            <Icon name="restart" className={cn(loading && "animate-spin")} />
          </IconButton>
        )}
      </header>

      {state === "loading" && (
        <div className={grid} role="status" aria-label={`正在读取${text.title}`}>
          {Array.from({ length: library === "cloud" ? 5 : 2 }, (_, index) => <TemplateCardSkeleton key={index} />)}
        </div>
      )}

      {state === "error" && (
        <Notice tone="danger" icon={<Icon name="cloud-cross" variant="duotone" />} title={`${text.title}读取失败`} action={<Button size="sm" onClick={onRefresh}><Icon name="restart" />重试</Button>}>
          <span className="font-mono text-xs text-fg-muted">{error}</span>
          <span className="mt-1 block">服务暂时连不上时，可以先在桌面客户端里使用本地模板。</span>
        </Notice>
      )}

      {state === "unavailable" && (
        <Notice icon={<Icon name="monitor" variant="duotone" />} title="本地模板需要桌面客户端">
          浏览器无法读取电脑上的文件，请在桌面客户端中查看和新建本地模板。
        </Notice>
      )}

      {state === "ready" && templates.length === 0 && (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
          <EmptyFrames />
          <p className="mt-5 text-sm font-medium">{text.empty}</p>
          <p className="mt-1 max-w-sm text-[13px] text-fg-muted">{text.emptyHint}</p>
          <Button size="sm" variant="secondary" className="mt-5" onClick={onCreate}>
            <Icon name="add-circle" />新建{text.title}
          </Button>
        </div>
      )}

      {state === "ready" && templates.length > 0 && (
        <div className={grid}>
          <AnimatePresence>
            {templates.map((template, index) => (
              <TemplateCard
                key={template.template_id}
                template={template}
                index={index}
                editing={template.template_id === openId}
                dirty={dirty}
                onOpen={() => onOpen(template)}
                onDelete={() => onDelete(template)}
                onCopyId={() => onCopyId(template)}
              />
            ))}
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
