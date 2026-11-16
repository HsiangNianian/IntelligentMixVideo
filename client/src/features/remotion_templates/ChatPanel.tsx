/** 当前会话的聊天展示与输入；每条消息最多附 4 张参考图（点击选择、拖入或粘贴），只作为生成参考，预览 URL 随组件卸载释放。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { cn } from "@/lib/utils";
import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowUp, ImagePlus, Square, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Hint } from "@/components/Hint";
import { Textarea } from "@/components/ui/textarea";
import { apiUrl } from "./api";
import { FIXED_COMPOSITION } from "./composition";
import { DurationPicker } from "./DurationPicker";
import { LoopRounds } from "./LoopRounds";
import { TaskStatus } from "./TaskStatus";
import { VersionCard } from "./VersionCard";
import { MAX_MESSAGE_IMAGES, type ChatMessage, type Job, type NewWork, type SessionJob, type Version } from "./model";
import type { Draft } from "./useTemplateSession";
import type { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import { LogoMark } from "@/components/studio/Logo";
import { LoaderGooeyBlobs } from "@/components/ui/loaders-gooey-blobs";
import { AnimatePresence, motion } from "motion/react";

/** 本地图片预览不上传到第三方，替换文件和清空会话时清理 object URL。 */
function ReferenceImage({ file }: { file: File }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const value = URL.createObjectURL(file);
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [file]);
  return (
    <img
      src={url || undefined}
      alt={`参考图片：${file.name}`}
      className="max-h-32 max-w-full rounded-lg object-contain"
    />
  );
}

/** 发送、图片和停止操作由工作区的统一任务锁控制；disabledReason 说明锁定原因与恢复时机。 */
interface Props {
  messages: ChatMessage[];
  busy: boolean;
  disabled: boolean;
  disabledReason?: string;
  canStop: boolean;
  first: boolean;
  /** 首条消息另带预览时长与「关键词字效」「双栏」选择；之后的消息不带。 */
  onSend: (text: string, images: File[], start?: NewWork) => void;
  onStop: () => void;
  draft?: Draft | null;
  job?: Job | SessionJob | null;
  jobs?: Record<string, SessionJob>;
  hasOlder?: boolean;
  olderLoading?: boolean;
  onOlder?: () => void;
  configuration?: ReactNode;
  versions?: Version[];
  latestVersionId?: string;
  previewVersionId?: string;
  code?: string;
  versionDisabled?: boolean;
  previewDisabled?: boolean;
  onPreviewVersion?: (id: string) => void;
  /** 用户改选过的资产分类（按版本 ID）；未改选的版本卡片显示 Agent 判断的分类。 */
  versionKinds?: Record<string, SpriteKind>;
  onVersionKindChange?: (id: string, kind: SpriteKind) => void;
  versionNotice?: ReactNode;
}
/** 只渲染公开消息；输入支持中文组合输入，Shift+Enter 换行，Enter 发送。 */
export function ChatPanel({
  messages,
  busy,
  disabled,
  disabledReason,
  canStop,
  first,
  onSend,
  onStop,
  draft,
  job,
  jobs = {},
  hasOlder,
  olderLoading,
  onOlder,
  configuration,
  versions = [],
  latestVersionId,
  previewVersionId,
  code,
  versionDisabled = false,
  previewDisabled = false,
  onPreviewVersion,
  versionKinds,
  onVersionKindChange,
  versionNotice,
}: Props) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [frames, setFrames] = useState<number | null>(FIXED_COMPOSITION.duration_in_frames);
  const [keywordEffect, setKeywordEffect] = useState(false);
  const [twoTier, setTwoTier] = useState(false);
  const [bilingual, setBilingual] = useState(false);
  const switchId = useId();
  useUpdateBlocker("聊天中还有未发送的内容或参考图", !!text || images.length > 0);
  const [dragging, setDragging] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const picker = useRef<HTMLInputElement>(null);
  // 成功任务通过稳定结果 ID 绑定卡片；旧消息缺少任务链路时仍在末尾保留版本入口。
  const anchors = new Map<string, string>();
  for (const message of messages) {
    const versionId = message.job_id
      ? jobs[message.job_id]?.result_version_id
      : null;
    if (versionId && (message.role === "assistant" || !anchors.has(versionId)))
      anchors.set(versionId, message.id);
  }
  /** 选择、拖放与粘贴共用：逐张校验格式与大小，合规的按顺序追加，每条最多 MAX_MESSAGE_IMAGES 张；锁定时不附图（输入区下方常驻锁定原因）。 */
  function attach(files: File[]) {
    if (disabled || !files.length) return;
    const valid = files.filter(
      (file) =>
        ["image/png", "image/jpeg", "image/webp"].includes(file.type) &&
        file.size <= 10 * 1024 * 1024,
    );
    const room = Math.max(MAX_MESSAGE_IMAGES - images.length, 0);
    if (valid.length && room) setImages([...images, ...valid.slice(0, room)]);
    setError(
      valid.length < files.length
        ? "请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片。"
        : valid.length > room
          ? `每条消息最多附 ${MAX_MESSAGE_IMAGES} 张参考图片。`
          : "",
    );
  }
  /** 代码、预览与选择各自绑定版本，不能从当前草稿推导历史内容。 */
  function resultCard(version: Version) {
    return (
      <VersionCard
        key={version.id}
        version={version}
        code={version.id === latestVersionId ? code : undefined}
        selected={version.id === previewVersionId}
        latest={version.id === latestVersionId}
        disabled={versionDisabled}
        previewDisabled={previewDisabled}
        onPreview={() => onPreviewVersion?.(version.id)}
        kind={versionKinds?.[version.id]}
        onKindChange={(kind) => onVersionKindChange?.(version.id, kind)}
      />
    );
  }
  // 未送达的草稿在渲染时直接回到输入框，与错误提示同一次提交，不出现空输入的中间帧；等待期间已输入的新内容优先保留。
  const [restored, setRestored] = useState(draft?.id);
  if (draft && draft.id !== restored) {
    setRestored(draft.id);
    setText((current) => current || draft.text);
    setImages((current) => (current.length ? current : draft.images));
  }
  // 历史分页只在头部插入消息；末尾新增回复才跟随到底部。
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [messages.at(-1)?.id, busy]);
  // 阶段和版本目录可能晚于消息到达；仅跟随底部时补滚动，避免打断历史阅读。
  const phaseCount = job && "progress" in job ? job.progress?.length : 0;
  useEffect(() => {
    if (following.current) end.current?.scrollIntoView?.({ block: "nearest" });
  }, [job?.id, phaseCount, versions.at(-1)?.id, versions.length]);
  /** 按钮与 Enter 共用防重复入口；等待期间保留文字草稿。 */
  function send() {
    if (disabled || (!text.trim() && !images.length) || (first && frames === null)) return;
    onSend(text, images, first ? { durationInFrames: frames!, keywordEffect, twoTier, bilingual } : undefined);
    setText("");
    setImages([]);
    setError("");
  }
  return (
    <section
      aria-label="字效聊天"
      className={cn("studio-chat flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-surface", !messages.length && "studio-chat-empty")}
    >
      <div className="flex h-14 shrink-0 items-center gap-2 border-b px-5 text-sm font-medium">
        <Sparkles className="size-4 text-brand" /> AI 字效{" "}
        <span className="ml-auto text-xs font-normal text-muted-foreground">
          当前会话
        </span>
      </div>
      {job && "created_at" in job && !job.progress?.length && (
        <TaskStatus job={job} />
      )}
      <div
        role="log"
        aria-label="聊天消息"
        aria-live="polite"
        className="studio-chat-log min-h-0 flex-1 space-y-5 overflow-x-hidden overflow-y-auto p-5"
        onScroll={(event) => {
          const log = event.currentTarget;
          following.current =
            log.scrollHeight - log.scrollTop - log.clientHeight < 60;
        }}
      >
        {hasOlder && (
          <Button
            variant="ghost"
            className="w-full"
            disabled={olderLoading}
            onClick={onOlder}
          >
            {olderLoading ? "正在加载…" : "加载更早消息"}
          </Button>
        )}
        {!messages.length && (
          <div className="flex flex-col items-center justify-center gap-3 pt-4 text-center text-sm">
            <LogoMark className="mb-2 size-11" />
            <h2 className="text-[23px] font-semibold tracking-tight">把想法，变成画面</h2>
            <p className="max-w-72 leading-6 text-muted-foreground">
              描述文字、颜色、位置和出场方式，也可以上传、拖入或粘贴参考图片。
            </p>
            <button
              type="button"
              className="mt-2 rounded-xl border bg-muted/40 p-3 text-left text-xs leading-5 hover:bg-muted"
              onClick={() =>
                setText(
                  "制作一个白色粗体居中标题，文字是「今日灵感」，带黄色下划线。",
                )
              }
            >
              试试：白色粗体居中标题，带黄色下划线 ↗
            </button>
          </div>
        )}
        {configuration}
        {versionNotice}
        {messages.map((message, index) => (
          <Fragment key={message.id}>
            <div
              key={message.id}
              className={cn(
                "flex",
                message.role === "user" ? "justify-end" : "justify-start",
              )}
            >
              <div
                className={cn(
                  "max-w-[90%] space-y-2 rounded-2xl px-4 py-3 text-sm leading-6",
                  message.role === "user"
                    ? "rounded-br-md bg-raised"
                    : "rounded-bl-md px-0 bg-transparent",
                )}
              >
                {!!(message.images?.length || message.image_asset_ids?.length) && (
                  <div className="flex flex-wrap gap-2">
                    {message.images?.map((file, index) => (
                      <ReferenceImage key={index} file={file} />
                    ))}
                    {message.image_asset_ids?.map((id) => (
                      <img
                        key={id}
                        src={apiUrl(`/assets/${encodeURIComponent(id)}`)}
                        alt="历史参考图片"
                        className="max-h-32 max-w-full rounded-lg object-contain"
                      />
                    ))}
                  </div>
                )}
                {message.created_at && (
                  <time
                    dateTime={message.created_at}
                    className="block font-mono text-[10px] text-fg-subtle"
                  >
                    {new Date(message.created_at).toLocaleString("zh-CN")}
                  </time>
                )}
                <p className="whitespace-pre-wrap break-words">
                  {message.text || "请参考这张图片制作字效。"}
                </p>
                {/* 最新回复里的建议方向：点选即作为下一条指令发送，与输入框发送同样受任务锁约束。 */}
                {message.role === "assistant" && !!message.suggestions?.length && index === messages.length - 1 && (
                  <div className="flex flex-wrap gap-2" aria-label="可选的修改方向">
                    {message.suggestions.map((suggestion) => (
                      <Button key={suggestion} type="button" variant="outline" size="sm" className="h-auto whitespace-normal py-1 text-left text-xs"
                        disabled={disabled} onClick={() => onSend(suggestion, [])}>
                        {suggestion}
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {message.job_id &&
            jobs[message.job_id]?.progress?.length &&
            messages.findIndex((item) => item.job_id === message.job_id) ===
              index ? (
              <TaskStatus job={jobs[message.job_id]} />
            ) : null}
            {message.job_id &&
            jobs[message.job_id]?.rounds?.length &&
            messages.findIndex((item) => item.job_id === message.job_id) ===
              index ? (
              <LoopRounds job={jobs[message.job_id]} />
            ) : null}
            {versions
              .filter((version) => anchors.get(version.id) === message.id)
              .map(resultCard)}
          </Fragment>
        ))}
        {versions.filter((version) => !anchors.has(version.id)).map(resultCard)}
        {job &&
          "created_at" in job &&
          !!job.progress?.length &&
          !messages.some((message) => message.job_id === job.id) && (
            <TaskStatus job={job} />
          )}
        {job &&
          "created_at" in job &&
          !!job.rounds?.length &&
          !messages.some((message) => message.job_id === job.id) && (
            <LoopRounds job={job} />
          )}
        {busy && !phaseCount && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <LoaderGooeyBlobs size={6} />
            正在处理…
          </div>
        )}
        <div ref={end} />
      </div>
      {/* 输入区接受拖入或粘贴参考图：拖放始终阻止浏览器默认打开文件，避免离开应用；未锁定时附图并显示遮罩。粘贴只截获图片，文字照常输入。 */}
      <form
        className={cn(
          "studio-composer relative m-4 mt-0 shrink-0 rounded-2xl border border-line-strong bg-surface p-3 shadow-sm transition-colors focus-within:border-brand/40 focus-within:ring-4 focus-within:ring-brand/5",
          dragging && "border-primary/60",
        )}
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(!disabled);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          attach(Array.from(event.dataTransfer.files));
        }}
        onPaste={(event) => {
          const files = Array.from(event.clipboardData.items)
            .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
            .map((item) => item.getAsFile())
            .filter((file): file is File => !!file);
          if (!files.length) return;
          event.preventDefault();
          attach(files);
        }}
      >
        <AnimatePresence>
          {dragging && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-1.5 rounded-xl bg-background/80 text-sm font-medium backdrop-blur-sm"
            >
              <ImagePlus className="size-6 text-muted-foreground" aria-hidden="true" />
              松开以添加参考图片
            </motion.div>
          )}
        </AnimatePresence>
        {!!images.length && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((file, index) => (
              <motion.div
                key={`${index}-${file.name}-${file.size}-${file.lastModified}`}
                initial={{ opacity: 0, scale: 0.85, filter: "blur(8px)" }}
                animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
                transition={{ type: "spring", duration: 0.4, bounce: 0 }}
                className="flex origin-top-left items-start gap-1"
              >
                <ReferenceImage file={file} />
                <Hint label={`移除参考图片：${file.name}`}>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`移除参考图片：${file.name}`}
                    disabled={disabled}
                    onClick={() => setImages(images.filter((_, other) => other !== index))}
                  >
                    <X />
                  </Button>
                </Hint>
              </motion.div>
            ))}
          </div>
        )}
        <Textarea
          aria-label="字效描述"
          placeholder="描述你想要的字效，或继续提出修改要求…"
          value={text}
          maxLength={8192}
          onChange={(event) => setText(event.target.value)}
          className="min-h-24 resize-none border-0 p-0 shadow-none focus-visible:ring-0"
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              send();
            }
          }}
        />
        {error && (
          <motion.p
            key={error}
            role="alert"
            animate={{ x: [0, 2, -2, 2, -2, 0] }}
            transition={{ duration: 0.3, delay: 0.1 }}
            className="py-2 text-xs text-destructive"
          >
            {error}
          </motion.p>
        )}
        {disabled && disabledReason && (
          <p className="pt-2 text-xs text-muted-foreground">{disabledReason}</p>
        )}
        {/* 窄聊天栏放不下时整行换行，发送按钮靠右另起一行，不被外层 overflow-hidden 裁掉。 */}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-1 gap-y-2">
          <input
            ref={picker}
            disabled={disabled}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            className="hidden"
            aria-label="上传参考图片"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              attach(files);
            }}
          />
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => picker.current?.click()}
            >
              <ImagePlus />
              图片
            </Button>
            {first && <DurationPicker disabled={disabled} onChange={setFrames} />}
            {first && (
              <div
                title="让关键词带自己的字效（如弹跳、发光、描边），之后可在本会话继续修改；未打开时由 Agent 按描述与参考图判断"
                className="flex items-center gap-1.5 px-2 text-xs text-muted-foreground"
              >
                <Switch id={`${switchId}-keyword`} checked={keywordEffect} disabled={disabled} onCheckedChange={setKeywordEffect} />
                <label htmlFor={`${switchId}-keyword`}>关键词字效</label>
              </div>
            )}
            {first && (
              <div
                title="生成上下两栏、各自样式的字效（如上栏小字引导语、下栏大字标题）；未打开时由 Agent 按描述与参考图判断"
                className="flex items-center gap-1.5 px-2 text-xs text-muted-foreground"
              >
                <Switch id={`${switchId}-tiers`} checked={twoTier} disabled={disabled} onCheckedChange={setTwoTier} />
                <label htmlFor={`${switchId}-tiers`}>双栏</label>
              </div>
            )}
            {first && (
              <div
                title="每组文字带中文与英文；同时开启双栏时生成上下两组、共四行，保留各组样式；未打开时由 Agent 按描述与参考图判断"
                className="flex items-center gap-1.5 px-2 text-xs text-muted-foreground"
              >
                <Switch id={`${switchId}-bilingual`} checked={bilingual} disabled={disabled} onCheckedChange={setBilingual} />
                <label htmlFor={`${switchId}-bilingual`}>双语字幕</label>
              </div>
            )}
          </div>
          {busy && canStop ? (
            <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={onStop}>
              <Square />
              停止
            </Button>
          ) : (
            <Button
              type="submit"
              size="sm"
              className="ml-auto bg-brand text-brand-ink hover:bg-brand-hover"
              disabled={disabled || (!text.trim() && !images.length) || (first && frames === null)}
            >
              发送
              <ArrowUp />
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
