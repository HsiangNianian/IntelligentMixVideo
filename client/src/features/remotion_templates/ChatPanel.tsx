/** 当前会话的聊天展示与输入；图片只作为生成参考，预览 URL 随组件卸载释放。 */
import { cn } from "@/lib/utils";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUp, ImagePlus, Square, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { apiUrl } from "./api";
import { TaskStatus } from "./TaskStatus";
import type { ChatMessage, Job, SessionJob } from "./model";

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
}: Props) {
  const [text, setText] = useState("");
  const [image, setImage] = useState<File>();
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const picker = useRef<HTMLInputElement>(null);
  // 历史分页只在头部插入消息；末尾新增回复才跟随到底部。
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [messages.at(-1)?.id, busy]);
  // 只在用户仍跟随底部时滚动新阶段，避免打断较早消息的阅读。
  const phaseCount = job && "progress" in job ? job.progress?.length : 0;
  useEffect(() => {
    if (following.current)
      end.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [phaseCount]);
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
      className="flex h-full min-h-0 flex-col rounded-2xl border bg-card shadow-sm"
    >
      <div className="flex items-center gap-2 border-b px-5 py-4 text-sm font-medium">
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
        className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5"
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
            <h2 className="text-lg font-semibold">把想法变成字效</h2>
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
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted",
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
          </Fragment>
        ))}
        {job &&
          "created_at" in job &&
          !!job.progress?.length &&
          !messages.some((message) => message.job_id === job.id) && (
            <TaskStatus job={job} />
          )}
        {busy && !phaseCount && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="size-2 animate-pulse rounded-full bg-primary" />
            正在处理…
          </p>
        )}
        <div ref={end} />
      </div>
      <form
        className="m-4 mt-0 rounded-xl border bg-background p-3"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        {image && (
          <div className="mb-2 flex items-start gap-2">
            <ReferenceImage file={image} />
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
          <p role="alert" className="py-2 text-xs text-destructive">
            {error}
          </p>
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
              if (disabled || !first) return;
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              if (
                !["image/png", "image/jpeg", "image/webp"].includes(
                  file.type,
                ) ||
                file.size > 10 * 1024 * 1024
              ) {
                setError("请选择不超过 10 MiB 的 PNG、JPEG 或 WebP 图片。");
                return;
              }
              setImage(file);
              setError("");
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
