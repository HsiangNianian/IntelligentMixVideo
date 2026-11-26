/**
 * 云端模板空间画廊：拖动、滚轮、箭头与索引浏览传入的最近记录；读取失败、空列表及删除仍走原业务回调。
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "../icons";
import { Button } from "../ui";
import type { TemplateSummary } from "../types";
import { TemplateCard, TemplateCardSkeleton } from "./TemplateCard";
import "./gallery.css";

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

/** 点击封面将其居中，箭头进入对应模板的编辑页；删除选中项后回落到首项。 */
export function TemplateSection({ state, templates, error, onRefresh, onCreate, editing, onOpen, onDelete, onCopyId }: SectionProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drag, setDrag] = useState(0);
  const stage = useRef<HTMLDivElement>(null);
  const pointer = useRef<{ id: number; x: number; pitch: number } | null>(null);
  const suppressClick = useRef(false);
  const wheel = useRef({ delta: 0, time: 0 });
  const selected = Math.max(0, templates.findIndex((item) => item.template_id === selectedId));
  const current = templates[selected];
  /** 按当前列表循环浏览，不维护另一份模板数据。 */
  function move(step: number) {
    if (templates.length) setSelectedId(templates[((selected + step) % templates.length + templates.length) % templates.length].template_id);
  }
  // 滚轮只在多张封面的陈列区接管；保留 Ctrl 缩放与区域外的页面滚动。
  useEffect(() => {
    const element = stage.current;
    if (!element || templates.length < 2) return;
    const scroll = (event: WheelEvent) => {
      if (event.ctrlKey) return;
      event.preventDefault();
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (event.timeStamp - wheel.current.time > 180 || Math.sign(delta) !== Math.sign(wheel.current.delta)) wheel.current.delta = 0;
      wheel.current.time = event.timeStamp;
      wheel.current.delta += delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      const steps = Math.trunc(wheel.current.delta / 100);
      if (steps) {
        setSelectedId(templates[((selected + steps) % templates.length + templates.length) % templates.length].template_id);
        wheel.current.delta %= 100;
      }
    };
    element.addEventListener("wheel", scroll, { passive: false });
    return () => element.removeEventListener("wheel", scroll);
  }, [selected, templates, state]);
  return (
    <section aria-label="模板列表" className="template-gallery space-y-4">
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

      {state === "loading" && <div className="mx-auto flex max-w-4xl items-center justify-center gap-5 py-10"><span role="status" className="sr-only">正在读取云端模板</span>{[0, 1, 2].map((index) => <div key={index} className="w-1/3 max-w-60"><TemplateCardSkeleton /></div>)}</div>}
      {state === "ready" && current && <>
        <div ref={stage} className="template-gallery-stage" aria-label="拖动或滚轮浏览模板" data-dragging={drag !== 0}
          onDragStart={(event) => event.preventDefault()}
          onPointerDown={(event) => {
            suppressClick.current = false;
            if (event.button !== 0 || templates.length < 2 || (event.target as HTMLElement).closest("[data-gallery-actions]")) return;
            const card = event.currentTarget.querySelector(".template-gallery-item") as HTMLElement;
            pointer.current = { id: event.pointerId, x: event.clientX, pitch: card.offsetWidth * .83 };
          }}
          onPointerMove={(event) => {
            const start = pointer.current;
            if (!start || start.id !== event.pointerId) return;
            const delta = start.x - event.clientX;
            if (Math.abs(delta) < 6 && !suppressClick.current) return;
            suppressClick.current = true;
            event.currentTarget.setPointerCapture(event.pointerId);
            setDrag(delta / start.pitch);
          }}
          onPointerUp={(event) => {
            if (pointer.current?.id !== event.pointerId) return;
            if (suppressClick.current) move(Math.round(drag));
            pointer.current = null;
            setDrag(0);
          }}
          onPointerCancel={() => { pointer.current = null; setDrag(0); }}
          onClickCapture={(event) => {
            if (suppressClick.current && event.detail !== 0) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; }
          }}
          onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1); }
        }}>
          {templates.map((template, index) => {
            let offset = ((index - selected - drag) % templates.length + templates.length) % templates.length;
            if (offset > templates.length / 2) offset -= templates.length;
            if (Math.abs(offset) > 2) return null;
            return <div key={template.template_id} className="template-gallery-item" data-offset={offset} data-distant={Math.abs(offset) > 1}
              style={{ "--offset": offset, "--distance": Math.abs(offset), zIndex: 3 - Math.abs(offset) } as CSSProperties}>
              <TemplateCard template={template} editing={template.template_id === editing?.id} dirty={editing?.dirty ?? false}
                onCenter={() => setSelectedId(template.template_id)}
                onOpen={() => onOpen(template)} onDelete={() => onDelete(template)} onCopyId={() => onCopyId(template)} />
            </div>;
          })}
        </div>
        <div className="mx-auto flex max-w-lg items-start justify-center gap-5 sm:gap-10">
          <Button variant="ghost" aria-label="上一个模板" disabled={templates.length < 2} onClick={() => move(-1)}>←</Button>
          <div className="min-w-0 flex-1 text-center">
            <h2 aria-label={current.name} className="sketch-text max-w-full truncate text-3xl leading-relaxed">{current.name}</h2>
            <p className="mt-1 line-clamp-2 text-xs text-fg-muted">{current.description || "暂无模板说明"}</p>
            <p className="mt-2 text-[11px] text-fg-subtle">{current.track_count ?? 0} 个对象 · {current.updated_at.slice(0, 10)}</p>
          </div>
          <Button variant="ghost" aria-label="下一个模板" disabled={templates.length < 2} onClick={() => move(1)}>→</Button>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-1" aria-label="浏览模板">
          {templates.map((template, index) => <button key={template.template_id} type="button" aria-label={`浏览模板：${template.name}`} aria-pressed={index === selected}
            onClick={() => setSelectedId(template.template_id)} className="flex size-7 items-center justify-center text-fg-muted hover:text-fg"><span className={index === selected ? "size-1.5 bg-fg" : "size-1.5 border border-line-strong"} /></button>)}
        </div>
      </>}
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
