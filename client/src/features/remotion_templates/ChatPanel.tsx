/** 当前会话的聊天展示与输入；参考图可点击选择或拖入输入区，只作为生成参考，预览 URL 随组件卸载释放。 */
import { cn } from "@/lib/utils";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUp, ImagePlus, Square, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/Hint";
import { Textarea } from "@/components/ui/textarea";
import { apiUrl } from "./api";
import { LoopRounds } from "./LoopRounds";
import { TaskStatus } from "./TaskStatus";
import { VersionCard } from "./VersionCard";
import type { ChatMessage, Job, SessionJob, Version } from "./model";
import { FlipText } from "@/components/ui/flip-text";
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

/** 发送、图片和停止操作由工作区的统一任务锁控制。 */
interface Props {
  messages: ChatMessage[];
  busy: boolean;
  disabled: boolean;
  canStop: boolean;
  first: boolean;
  onSend: (text: string, image?: File) => void;
  onStop: () => void;
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
  versionNotice?: ReactNode;
}
/** 只渲染公开消息；输入支持中文组合输入，Shift+Enter 换行，Enter 发送。 */
export function ChatPanel({
  messages,
  busy,
  disabled,
  canStop,
  first,
  onSend,
  onStop,
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
  versionNotice,
}: Props) {
  const [text, setText] = useState("");
  const [image, setImage] = useState<File>();
  const [error, setError] = useState("");
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
  /** 选择与拖放共用的参考图校验；仅首次请求可附图，不合规时显示错误并保留原图。 */
  function attach(file: File | undefined) {
    if (disabled || !first || !file) return;
    if (
      !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
      file.size > 10 * 1024 * 1024
    ) {
      setError("请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片。");
      return;
    }
    setImage(file);
    setError("");
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
      />
    );
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
    if (disabled || (!text.trim() && !image)) return;
    onSend(text, image);
    setText("");
    setImage(undefined);
    setError("");
  }
  return (
    <section
      aria-label="字效聊天"
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border bg-card"
    >
      <div className="flex h-14 shrink-0 items-center gap-2 border-b px-5 text-sm font-medium">
        <Sparkles className="size-4 text-primary" /> 字效助手{" "}
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
        className="min-h-0 flex-1 space-y-5 overflow-x-hidden overflow-y-auto p-4 xl:p-5"
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
          <div className="flex min-h-52 flex-col justify-center gap-3 text-sm">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10">
              <Sparkles className="size-5 text-primary" />
            </div>
            <h2 className="text-lg font-semibold"><FlipText loop={false} duration={1.6}>把想法变成字效</FlipText></h2>
            <p className="max-w-72 leading-6 text-muted-foreground">
              描述文字、颜色、位置和出场方式，也可以上传一张参考图片。
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
                    ? "rounded-br-md bg-primary/15 ring-1 ring-primary/25"
                    : "rounded-bl-md bg-muted",
                )}
              >
                {message.image && <ReferenceImage file={message.image} />}
                {message.image_asset_id && (
                  <img
                    src={apiUrl(
                      `/assets/${encodeURIComponent(message.image_asset_id)}`,
                    )}
                    alt="历史参考图片"
                    className="max-h-32 max-w-full rounded-lg object-contain"
                  />
                )}
                {message.created_at && (
                  <time
                    dateTime={message.created_at}
                    className="block text-xs opacity-70"
                  >
                    {new Date(message.created_at).toLocaleString("zh-CN")}
                    {message.reconstructed ? " · 历史恢复" : ""}
                  </time>
                )}
                <p className="whitespace-pre-wrap break-words">
                  {message.text || "请参考这张图片制作字效。"}
                </p>
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
      {/* 输入区接受拖入参考图：始终阻止浏览器默认打开文件，避免离开应用；仅首次请求且未锁定时附图并显示遮罩。 */}
      <form
        className={cn(
          "relative m-4 mt-0 rounded-xl border bg-background p-3 transition-colors",
          dragging && "border-primary/60",
        )}
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(!disabled && first);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          attach(event.dataTransfer.files[0]);
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
        {image && (
          <motion.div
            key={image.name + image.size}
            initial={{ opacity: 0, scale: 0.85, filter: "blur(8px)" }}
            animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
            transition={{ type: "spring", duration: 0.4, bounce: 0 }}
            className="mb-2 flex origin-top-left items-start gap-2"
          >
            <ReferenceImage file={image} />
            <Hint label="移除参考图片">
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="移除参考图片"
                disabled={disabled}
                onClick={() => setImage(undefined)}
              >
                <X />
              </Button>
            </Hint>
          </motion.div>
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
        <div className="mt-2 flex items-center justify-between">
          <input
            ref={picker}
            disabled={!first || disabled}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            aria-label="上传参考图片"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              attach(file);
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!first || disabled}
            onClick={() => picker.current?.click()}
          >
            <ImagePlus />
            图片
          </Button>
          {busy && canStop ? (
            <Button type="button" variant="outline" size="sm" onClick={onStop}>
              <Square />
              停止
            </Button>
          ) : (
            <Button
              type="submit"
              size="sm"
              disabled={disabled || (!text.trim() && !image)}
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
