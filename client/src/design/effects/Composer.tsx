/**
 * AI 字效的输入区与新对话的空状态，形式参考 v0、Bolt 与 AI Elements 的 PromptInput。
 * 输入区：上方文字（自动增高）、下方工具行（参考图、画布设置、发送）；最多 4 张参考图，点击、拖入或粘贴添加，
 * 拖入时整块显示投放提示。回车发送、Shift + 回车换行；生成中发送键变为停止键。
 * 不能发送时，原因以一行小字显示在输入框上方，输入框本身保持干净。
 * 空状态：标题、居中的输入框与下方一排灵感提示，点提示填入输入框；发送第一条后输入框落到对话底部。
 * 未发送的图片地址在移除或卸载时释放，已发送的交给页面在离开时释放。
 */
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { Popover } from "radix-ui";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { NumberField, Row, Segmented, Slider } from "../controls";
import { Icon } from "../icons";
import { LogoMark } from "../Logo";
import { Tooltip } from "../ui";
import { canvasPresets, suggestions, type Composition } from "./model";

interface ComposerProps {
  value: string;
  onValueChange: (value: string) => void;
  lockedReason: string | null;
  running: boolean;
  placeholder: string;
  composition: Composition;
  /** 只有还没发送过消息的新对话可以修改画布设置。 */
  onCompositionChange?: (composition: Composition) => void;
  onSend: (text: string, images: string[]) => void;
  onStop: () => void;
  /** 每次变化时把光标移进输入框（例如点了追问建议）。 */
  focusSignal?: number;
  /** hero：空状态里居中的大输入框；dock：对话底部。 */
  variant?: "hero" | "dock";
}

const accepted = ["image/png", "image/jpeg", "image/webp"];

/** 输入区本体。 */
export function Composer({ value, onValueChange, lockedReason, running, placeholder, composition, onCompositionChange, onSend, onStop, focusSignal, variant = "dock" }: ComposerProps) {
  const [images, setImages] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pending = useRef<string[]>([]);
  pending.current = images;
  useEffect(() => () => pending.current.forEach((url) => URL.revokeObjectURL(url)), []);
  const hero = variant === "hero";

  // 文字自动增高：空状态最少三行，底部最多约六行。
  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(Math.max(element.scrollHeight, hero ? 76 : 0), 160)}px`;
  }, [value, hero]);

  useEffect(() => {
    if (!focusSignal) return;
    const element = textarea.current;
    element?.focus();
    element?.setSelectionRange(element.value.length, element.value.length);
  }, [focusSignal]);

  /** 加入图片：过滤格式与大小，总数不超过 4 张。 */
  const add = (files: File[]) => {
    const valid = files.filter((file) => accepted.includes(file.type) && file.size <= 10 * 1024 * 1024);
    if (valid.length < files.length) toast.error("请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片");
    const room = 4 - images.length;
    if (valid.length > room) toast.error("每条消息最多附 4 张参考图");
    setImages((list) => [...list, ...valid.slice(0, room).map((file) => URL.createObjectURL(file))]);
  };
  const remove = (url: string) => {
    URL.revokeObjectURL(url);
    setImages((list) => list.filter((item) => item !== url));
  };

  const locked = Boolean(lockedReason);
  const canSend = !locked && (value.trim().length > 0 || images.length > 0);
  const send = () => {
    if (!canSend) return;
    onSend(value.trim(), images);
    setImages([]);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    if (!locked) add([...event.dataTransfer.files]);
  };
  const onPaste = (event: ClipboardEvent) => {
    const files = [...event.clipboardData.files].filter((file) => file.type.startsWith("image/"));
    if (files.length) {
      event.preventDefault();
      add(files);
    }
  };

  return (
    <div>
      {lockedReason && (
        <p className="mb-2 flex items-center justify-center gap-1.5 px-2 text-[11.5px] text-fg-subtle">
          <Icon name="lock-keyhole-minimalistic" className="size-3.5 shrink-0" />
          {lockedReason}
        </p>
      )}
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!locked) setDragging(true);
        }}
        onDragLeave={(event) => !event.currentTarget.contains(event.relatedTarget as Node) && setDragging(false)}
        onDrop={onDrop}
        onClick={(event) => event.target === event.currentTarget && textarea.current?.focus()}
        className={cn(
          "relative rounded-[22px] border bg-surface transition-[border-color,box-shadow] duration-200",
          hero ? "shadow-[0_1px_2px_oklch(0.2_0.01_265/0.05),0_12px_32px_-12px_oklch(0.2_0.01_265/0.18)]" : "shadow-[0_1px_2px_oklch(0.2_0.01_265/0.05),0_8px_24px_-14px_oklch(0.2_0.01_265/0.2)]",
          locked ? "border-line" : "border-line-strong focus-within:border-accent/45 focus-within:shadow-[0_0_0_4px_color-mix(in_oklch,var(--color-accent)_10%,transparent),0_8px_24px_-14px_oklch(0.2_0.01_265/0.2)]",
        )}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-1 z-10 flex items-center justify-center gap-2 rounded-[18px] border-2 border-dashed border-accent/60 bg-accent/[0.06] text-[13px] font-medium text-accent backdrop-blur-[2px]">
            <Icon name="gallery-add" variant="duotone" className="size-5" />
            松开即可添加参考图
          </div>
        )}
        {images.length > 0 && (
          <div className="flex gap-2 px-3 pt-3">
            {images.map((url) => (
              <span key={url} className="group relative size-14 overflow-hidden rounded-xl ring-1 ring-line" style={{ background: `center / cover url(${url})` }}>
                <button type="button" aria-label="移除这张图片" onClick={() => remove(url)} className="absolute right-1 top-1 flex size-5 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 [&_svg]:size-3">
                  <Icon name="close" />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={textarea}
          rows={1}
          value={value}
          disabled={locked}
          placeholder={placeholder}
          onChange={(event) => onValueChange(event.target.value)}
          onPaste={onPaste}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
          className={cn("block w-full resize-none bg-transparent px-4 pb-1 text-fg outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed", hero ? "pt-4 text-[15px] leading-relaxed" : "pt-3.5 text-[14px] leading-relaxed")}
        />
        <div className="flex items-center gap-1 px-2.5 pb-2.5 pt-1">
          <Tooltip label={`添加参考图 · ${images.length}/4 · 也可以拖入或粘贴`}>
            <button
              type="button"
              aria-label="添加参考图"
              disabled={locked || images.length >= 4}
              onClick={() => fileInput.current?.click()}
              className="flex size-8 items-center justify-center rounded-full border border-line text-fg-muted transition-colors hover:border-line-strong hover:bg-raised hover:text-fg disabled:opacity-40 [&_svg]:size-4"
            >
              <Icon name="gallery-add" />
            </button>
          </Tooltip>
          <input
            ref={fileInput}
            type="file"
            accept={accepted.join(",")}
            multiple
            hidden
            onChange={(event) => {
              add([...(event.target.files ?? [])]);
              event.target.value = "";
            }}
          />
          <CompositionChip composition={composition} onChange={onCompositionChange} />
          <span className="flex-1" />
          {running ? (
            <Tooltip label="停止生成">
              <button type="button" aria-label="停止生成" onClick={onStop} className="flex size-8 items-center justify-center rounded-full bg-fg text-canvas transition-transform hover:scale-105 active:scale-95">
                <span className="size-2.5 rounded-[3px] bg-current" />
              </button>
            </Tooltip>
          ) : (
            <Tooltip label="发送（Enter）">
              <button
                type="button"
                aria-label="发送"
                disabled={!canSend}
                onClick={send}
                className="flex size-8 items-center justify-center rounded-full bg-accent text-accent-ink shadow-[inset_0_1px_0_oklch(1_0_0/0.25)] transition-[transform,background-color,color] hover:scale-105 active:scale-95 disabled:bg-raised disabled:text-fg-subtle disabled:shadow-none disabled:hover:scale-100 [&_svg]:size-4"
              >
                <Icon name="arrow-up" />
              </button>
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  );
}

/** 画布设置胶囊：新对话可点开修改，开始后只读并说明原因。 */
function CompositionChip({ composition, onChange }: { composition: Composition; onChange?: (composition: Composition) => void }) {
  const preset = canvasPresets.find((item) => item.width === composition.width && item.height === composition.height);
  const label = `${preset?.label ?? `${composition.width}×${composition.height}`} · ${composition.seconds} 秒`;
  const chip = "flex h-8 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 text-xs text-fg-muted";
  const shape = <span className={cn("rounded-[2px] border-[1.5px] border-current", composition.width > composition.height ? "h-2 w-3" : composition.width < composition.height ? "h-3 w-2" : "size-2.5")} />;
  if (!onChange) {
    return (
      <Tooltip label={`${composition.width} × ${composition.height} · ${composition.fps} fps · 对话开始后不能修改`}>
        <span className={chip}>
          {shape}
          {label}
        </span>
      </Tooltip>
    );
  }
  return (
    <Popover.Root>
      <Popover.Trigger className={cn(chip, "border border-line transition-colors hover:border-line-strong hover:bg-raised hover:text-fg data-[state=open]:bg-raised data-[state=open]:text-fg")}>
        {shape}
        {label}
        <Icon name="alt-arrow-down" className="size-3" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="top" align="start" sideOffset={8} className="z-[70] w-80 space-y-3.5 rounded-2xl border border-line-strong bg-overlay p-4 shadow-pop data-[state=open]:animate-pop-in">
          <p className="text-[13px] font-medium">画布与预览时长</p>
          <Row label="画布">
            <Segmented
              size="sm"
              className="flex-1"
              label="画布"
              value={`${composition.width}x${composition.height}`}
              onChange={(next) => {
                const picked = canvasPresets.find((item) => `${item.width}x${item.height}` === next)!;
                onChange({ ...composition, width: picked.width, height: picked.height });
              }}
              options={canvasPresets.map((item) => ({ value: `${item.width}x${item.height}`, label: item.label }))}
            />
          </Row>
          <Row label="帧率">
            <Segmented size="sm" className="flex-1" label="帧率" value={String(composition.fps)} onChange={(fps) => onChange({ ...composition, fps: Number(fps) })} options={["24", "30", "60"].map((fps) => ({ value: fps, label: `${fps} fps` }))} />
          </Row>
          <Row label="预览时长">
            <Slider label="预览时长" value={composition.seconds} min={1} max={30} step={0.5} onChange={(seconds) => onChange({ ...composition, seconds })} />
            <NumberField label="预览时长" className="w-[64px] shrink-0" unit="秒" step={0.5} value={composition.seconds} min={1} max={30} onChange={(seconds) => onChange({ ...composition, seconds })} />
          </Row>
          <p className="text-[11px] leading-relaxed text-fg-subtle">{composition.width} × {composition.height}。预览时长只决定生成与预览的长度，最终时长由合成视频决定；对话开始后不能再修改。</p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** 空状态的标题区：标志、问句与一行说明。输入框与灵感提示由页面放在它下面。 */
export function EmptyHeading() {
  return (
    <div className="flex flex-col items-center text-center">
      <LogoMark className="size-11 drop-shadow-[0_8px_20px_color-mix(in_oklch,var(--color-brand-to)_35%,transparent)]" />
      <h2 className="mt-5 text-xl font-semibold tracking-tight">想做什么样的字效？</h2>
      <p className="mt-1.5 text-[13px] text-fg-muted">描述文字、颜色、动画和位置，也可以拖入参考图</p>
    </div>
  );
}

/** 灵感提示：一排圆角胶囊，点击填入输入框。 */
export function Suggestions({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-wrap justify-center gap-2">
      {suggestions.map((item) => (
        <Tooltip key={item.title} label={item.prompt}>
          <button type="button" onClick={() => onPick(item.prompt)} className="flex h-8 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-[12.5px] text-fg-muted transition-colors hover:border-accent/40 hover:bg-accent/[0.04] hover:text-fg [&_svg]:size-3.5">
            <Icon name={item.icon} className="text-accent" />
            {item.title}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
