/**
 * AI 字效对话里的消息，排版参考 Vercel AI Elements（Message / Reasoning / Tool / Artifact / Suggestion）：
 * - 用户消息：右侧浅灰气泡，参考图在气泡上方；不显示时间。
 * - 助手消息：不加头像，正文直接排在对话栏里。处理过程是一行灰字（进行中时文字流光），展开后是一条竖线串起的阶段列表，
 *   每次工具调用占一行，显示工具名与结果，点开查看参数。等待补充与失败用左侧色条标注，不用整块色底。
 * - 版本卡片：整张卡点击即预览，右侧是代码与保存到资产；正在预览的卡片带强调色描边。
 * - 追问建议：最新版本下方的一排圆角提示，点击填入输入框。
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Popover } from "radix-ui";
import { cn } from "@/lib/utils";
import { Segmented } from "../controls";
import { Icon, type IconName } from "../icons";
import { Button, IconButton, Spinner, Tooltip } from "../ui";
import { EffectFrame } from "./PreviewPanel";
import { referenceShots, sourceOf, stagePlan, type JobStatus, type Message, type Round, type Stage, type Version } from "./model";

/** 缩略图用深色棋盘格表示透明，浅色文字也看得清。 */
const checker = "repeating-conic-gradient(oklch(0.24 0.004 265) 0 25%, oklch(0.2 0.004 265) 0 50%) 0 0 / 8px 8px";

/** 用户消息：参考图在上，浅灰气泡在下，右对齐。图片是示意画面序号或本地图片地址。 */
export function UserMessage({ text, images }: { text: string; images: (number | string)[] }) {
  return (
    <div className="flex flex-col items-end gap-2 pl-12">
      {images.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1.5">
          {images.map((image, index) => (
            <span key={index} className="size-[72px] overflow-hidden rounded-xl ring-1 ring-line" style={{ background: typeof image === "number" ? referenceShots[image] : `center / cover url(${image})` }} />
          ))}
        </div>
      )}
      {text && <p className="whitespace-pre-wrap rounded-[18px] bg-raised px-4 py-2.5 text-[14px] leading-[1.65] text-fg">{text}</p>}
    </div>
  );
}

interface AssistantMessageProps {
  message: Extract<Message, { role: "assistant" }>;
  version?: Version;
  latest: boolean;
  previewing: boolean;
  onPreview: () => void;
  onRetry: () => void;
  onSaveAsset: (name: string, kind: Version["kind"]) => void;
}

/** 助手消息。 */
export function AssistantMessage({ message, version, latest, previewing, onPreview, onRetry, onSaveAsset }: AssistantMessageProps) {
  const { status } = message;
  return (
    <div className="space-y-3">
      {message.stages.length > 0 && <Process stages={message.stages} status={status} />}
      {status === "clarify" && (
        <Callout tone="warning" icon="question-circle" label="需要你补充">
          <p className="text-[14px] leading-[1.7] text-fg">{message.question}</p>
          <p className="mt-1 text-xs text-fg-subtle">直接在下方回答，回答后接着生成。</p>
        </Callout>
      )}
      {status === "failed" && (
        <Callout tone="danger" icon="danger-circle" label="没有生成成功">
          <p className="text-[13.5px] leading-[1.7] text-fg-muted">{message.error}</p>
          <button type="button" onClick={onRetry} className="mt-1.5 flex items-center gap-1 text-xs font-medium text-accent hover:underline [&_svg]:size-3.5">
            <Icon name="restart" />
            重新生成
          </button>
        </Callout>
      )}
      {status === "stopped" && (
        <p className="flex items-center gap-1.5 text-[13px] text-fg-muted">
          已停止，没有产生新版本 ·
          <button type="button" onClick={onRetry} className="text-accent hover:underline">重新生成</button>
        </p>
      )}
      {message.text && <p className="text-[14px] leading-[1.75] text-fg">{message.text}</p>}
      {version && <VersionCard version={version} latest={latest} previewing={previewing} onPreview={onPreview} onSaveAsset={onSaveAsset} />}
    </div>
  );
}

/** 处理过程标题行前的图标：进行中转圈，完成为勾；失败、等待补充、停止由下方提示说明，这里只用中性的时钟。 */
function StatusIcon({ status }: { status: JobStatus }) {
  if (status === "running" || status === "queued") return <Spinner className="size-3.5 text-accent" />;
  if (status === "done" || status === "answered") return <Icon name="check-circle" className="size-3.5" />;
  return <Icon name="clock-circle" className="size-3.5" />;
}

/**
 * 处理过程：一行灰字，进行中时显示当前阶段（流光）与实时计时并自动展开；结束后收起成「已完成 · 用时 N 秒」。
 * 展开后左侧一条竖线串起各阶段，有工具调用的阶段下面列出每次调用。
 */
function Process({ stages, status }: { stages: Stage[]; status: JobStatus }) {
  const running = status === "running";
  const [open, setOpen] = useState(running);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (running) setOpen(true);
  }, [running]);
  // 每进入一个新阶段，当前阶段的计时从零开始；进行中每秒加一，结束或卸载时停止。
  useEffect(() => setTick(0), [stages.length]);
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, [running]);
  const total = stages.reduce((sum, stage) => sum + stage.seconds, 0) + (running ? tick : 0);
  const current = stages[stages.length - 1];
  const summary = { done: "已完成", answered: "已回答", failed: "处理过程", stopped: "处理过程", clarify: "处理过程", queued: "排队中", running: "" }[status];
  return (
    <div>
      <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="group flex max-w-full items-center gap-1.5 text-[12.5px] text-fg-subtle transition-colors hover:text-fg-muted">
        <StatusIcon status={status} />
        {running ? (
          <span className="animate-text-shimmer truncate bg-[linear-gradient(90deg,var(--color-fg-subtle)_30%,var(--color-fg)_50%,var(--color-fg-subtle)_70%)] bg-size-[200%_100%] bg-clip-text font-medium text-transparent">{current.label}…</span>
        ) : (
          <span className="truncate">{summary}</span>
        )}
        <span className="shrink-0 tabular-nums">· {running ? `${total} 秒` : `用时 ${total} 秒`}</span>
        <Icon name="alt-arrow-down" className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="ml-[6.5px] mt-2 border-l border-line-strong pl-4">
          {stages.map((stage, index) => {
            const active = running && index === stages.length - 1;
            return (
              <li key={stage.label} className="py-1">
                <div className="flex items-center gap-2 text-[12.5px]">
                  <span className={cn("flex-1", active ? "font-medium text-fg" : "text-fg-muted")}>{stage.label}</span>
                  <span className="font-mono text-[10.5px] tabular-nums text-fg-subtle">{active ? stage.seconds + tick : stage.seconds}s</span>
                </div>
                {stage.rounds && <Rounds rounds={stage.rounds} />}
              </li>
            );
          })}
          {running && stagePlan.indexOf(current.label) < stagePlan.length - 1 && (
            <li className="py-1 text-[11.5px] text-fg-subtle/80">接下来：{stagePlan.slice(stagePlan.indexOf(current.label) + 1).join(" · ")}</li>
          )}
        </ol>
      )}
    </div>
  );
}

/** 工具调用：默认只显示次数，展开后每次调用一行。 */
function Rounds({ rounds }: { rounds: Round[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex items-center gap-1 text-[11.5px] text-fg-subtle hover:text-fg-muted">
        {rounds.length} 次工具调用
        <Icon name="alt-arrow-down" className={cn("size-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-1.5 space-y-1">
          {rounds.map((round, index) => <ToolCall key={index} round={round} />)}
        </div>
      )}
    </div>
  );
}

/** 一次工具调用：工具名、层级与结果，点开显示参数原文。 */
function ToolCall({ round }: { round: Round }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
        <Icon name="code" className="size-3.5 shrink-0 text-fg-subtle" />
        <span className="truncate font-mono text-[11.5px] text-fg">{round.tool}</span>
        <span className="shrink-0 text-[10.5px] text-fg-subtle">{round.layer}</span>
        <span className={cn("ml-auto flex shrink-0 items-center gap-1 rounded-full px-1.5 py-px text-[10px]", round.ok ? "bg-success/10 text-success" : "bg-danger/10 text-danger")}>
          <Icon name={round.ok ? "check-circle" : "danger-circle"} className="size-3" />
          {round.ok ? "完成" : "出错"}
        </span>
        <Icon name="alt-arrow-down" className={cn("size-3 shrink-0 text-fg-subtle transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-line bg-sunken px-2.5 py-2">
          <p className="mb-1 text-[10px] tracking-wider text-fg-subtle">参数</p>
          <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-fg-muted">{JSON.stringify(JSON.parse(round.args), null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

/** 提示块：左侧色条加小标题，用于等待补充与失败。 */
function Callout({ tone, icon, label, children }: { tone: "warning" | "danger"; icon: IconName; label: string; children: ReactNode }) {
  return (
    <div className={cn("border-l-2 py-0.5 pl-3.5", tone === "warning" ? "border-warning" : "border-danger")}>
      <p className={cn("mb-1 flex items-center gap-1.5 text-xs font-medium", tone === "warning" ? "text-warning" : "text-danger")}>
        <Icon name={icon} variant="duotone" className="size-3.5" />
        {label}
      </p>
      {children}
    </div>
  );
}

/**
 * 版本卡片：左侧竖版缩略画面，中间版本名与说明，右侧代码与保存到资产；点击卡片其余位置即预览。
 * 展开代码时把卡片底部滚进可见区域。
 */
function VersionCard({ version, latest, previewing, onPreview, onSaveAsset }: { version: Version; latest: boolean; previewing: boolean; onPreview: () => void; onSaveAsset: AssistantMessageProps["onSaveAsset"] }) {
  const [code, setCode] = useState(false);
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (code) card.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [code]);
  return (
    <div
      ref={card}
      className={cn(
        "group/card overflow-hidden rounded-2xl border bg-surface transition-[border-color,box-shadow] duration-200",
        previewing ? "border-accent/50 shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-accent)_10%,transparent)]" : "border-line hover:border-line-strong hover:shadow-lift",
      )}
    >
      <div className="relative flex items-center gap-3 p-2.5 pr-2">
        {/* 整卡点击预览：按钮铺满卡片头部，右侧的操作按钮叠在它上面。 */}
        <button type="button" aria-label={`预览 V${version.number}`} onClick={onPreview} className="absolute inset-0 rounded-2xl" />
        <span className="pointer-events-none relative h-[60px] w-[34px] shrink-0 overflow-hidden rounded-md ring-1 ring-line" style={{ background: checker }}>
          <EffectFrame params={version.params} time={2.5} width={760} />
        </span>
        <span className="pointer-events-none relative min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[13.5px] font-medium">{version.title}</span>
            <span className="shrink-0 rounded-md bg-raised px-1.5 font-mono text-[10.5px] leading-[18px] text-fg-muted">V{version.number}</span>
            {latest && <span className="shrink-0 rounded-md bg-accent/10 px-1.5 text-[10.5px] leading-[18px] text-accent">最新</span>}
          </span>
          <span className="mt-0.5 flex items-center gap-1.5 truncate text-[11.5px] text-fg-subtle">
            {previewing ? (
              <span className="flex items-center gap-1 text-accent"><span className="size-1.5 rounded-full bg-accent" />正在预览</span>
            ) : (
              <span className="transition-colors group-hover/card:text-fg-muted">点击预览</span>
            )}
            · {version.source === "agent" ? "智能体生成" : "手动调整"} · {version.time}
          </span>
        </span>
        <span className="relative flex shrink-0 items-center gap-0.5">
          <IconButton label={code ? "收起代码" : "查看代码"} onClick={() => setCode((value) => !value)} className={cn(code && "bg-raised text-fg")}>
            <Icon name="code" />
          </IconButton>
          <SaveAsset version={version} onSave={onSaveAsset} />
        </span>
      </div>
      {code && <CodeBlock source={sourceOf(version)} />}
    </div>
  );
}

/** 保存到资产：确认名称与分类（默认取智能体判断的分类）。 */
function SaveAsset({ version, onSave }: { version: Version; onSave: AssistantMessageProps["onSaveAsset"] }) {
  const [name, setName] = useState(version.title);
  const [kind, setKind] = useState<Version["kind"]>(version.kind);
  return (
    <Popover.Root>
      <Tooltip label="保存到资产，之后可以在模板编辑里使用">
        <Popover.Trigger className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-fg-muted transition-colors hover:bg-raised hover:text-fg data-[state=open]:bg-raised data-[state=open]:text-fg [&_svg]:size-4">
          <Icon name="archive-down-minimalistic" />
          保存
        </Popover.Trigger>
      </Tooltip>
      <Popover.Portal>
        <Popover.Content side="top" align="end" sideOffset={8} className="z-[70] w-80 space-y-3 rounded-2xl border border-line-strong bg-overlay p-4 shadow-pop data-[state=open]:animate-pop-in">
          <div>
            <p className="text-[13px] font-medium">保存到资产</p>
            <p className="mt-0.5 text-[11.5px] leading-relaxed text-fg-subtle">保存后可以在模板编辑里添加，资产库所有人可见。</p>
          </div>
          <input
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
            className="h-9 w-full rounded-lg border border-line bg-sunken px-2.5 text-[13px] text-fg outline-none focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15"
          />
          <div className="space-y-1.5">
            <p className="text-[11px] text-fg-subtle">分类 · 默认取智能体判断的「{version.kind}」</p>
            <Segmented size="sm" label="资产分类" value={kind} onChange={setKind} options={(["字效", "滤镜", "视频特效", "转场"] as const).map((value) => ({ value, label: value }))} />
          </div>
          <div className="flex justify-end gap-2">
            <Popover.Close asChild><Button size="sm" variant="ghost">取消</Button></Popover.Close>
            <Popover.Close asChild>
              <Button size="sm" variant="primary" disabled={!name.trim()} onClick={() => onSave(name.trim(), kind)}>保存</Button>
            </Popover.Close>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** 关键字与字符串的展示用着色，只影响阅读颜色。 */
function colorize(line: string): ReactNode[] {
  return line.split(/("[^"]*"|`[^`]*`|\b(?:import|from|export|const|function|return|default|typeof)\b)/).map((part, index) =>
    /^["`]/.test(part) ? <span key={index} className="text-spectrum-3">{part}</span> : /^(import|from|export|const|function|return|default|typeof)$/.test(part) ? <span key={index} className="text-accent">{part}</span> : part,
  );
}

/** 源码：文件名、检查结果与复制，下方带行号的代码。 */
function CodeBlock({ source }: { source: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="border-t border-line bg-sunken">
      <div className="flex items-center gap-2 px-3 py-1.5 text-[11px]">
        <span className="font-mono text-fg-muted">Export.tsx</span>
        <span className="flex items-center gap-1 text-success"><Icon name="check-circle" className="size-3" />检查通过</span>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(source);
            setCopied(true);
          }}
          className="ml-auto flex h-6 items-center gap-1 rounded-md px-1.5 text-fg-subtle transition-colors hover:bg-raised hover:text-fg [&_svg]:size-3.5"
        >
          <Icon name={copied ? "check-circle" : "copy"} />
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre className="max-h-64 overflow-auto border-t border-line py-2 font-mono text-[11px] leading-[1.65]">
        {source.split("\n").map((line, index) => (
          <div key={index} className="flex">
            <span className="w-9 shrink-0 select-none pr-3 text-right text-fg-subtle/60">{index + 1}</span>
            <span className="whitespace-pre pr-4 text-fg">{colorize(line)}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}

/** 追问建议：一排圆角提示，点击填入输入框。 */
export function FollowUps({ items, onPick }: { items: string[]; onPick: (text: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <button key={item} type="button" onClick={() => onPick(item)} className="flex h-7 items-center gap-1 rounded-full border border-line bg-surface px-3 text-xs text-fg-muted transition-colors hover:border-accent/40 hover:bg-accent/[0.05] hover:text-fg">
          {item}
        </button>
      ))}
    </div>
  );
}
