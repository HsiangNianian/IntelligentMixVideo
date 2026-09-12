/** 隔离预览容器；仅向当前成功版本的 iframe 发送参数与背景直链，清理消息监听和就绪超时。 */
import { useEffect, useRef, useState } from "react";
import { Film, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiUrl } from "./api";
import { backgroundUrl, type Values, type Version } from "./model";

/** 背景链接不进入生成请求，模板切换后的旧 iframe 消息不会影响当前画面。 */
export function PreviewPanel({
  version,
  values,
  pending,
  onBusyChange,
}: {
  version: Version | null;
  values: Values;
  pending: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const [link, setLink] = useState("");
  const [background, setBackground] = useState("");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [reload, setReload] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const channel = useRef("");
  const requestId = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  /** 每次加载都有超时出口；播放本身不触发锁定。 */
  function begin() {
    window.clearTimeout(timer.current);
    setRendering(true);
    onBusyChange(true);
    timer.current = window.setTimeout(() => {
      setError("预览加载超时，请检查服务连接后重试。");
      setRendering(false);
      onBusyChange(false);
    }, 20_000);
  }
  /** 完成或失败都释放操作锁，旧请求的完成消息由调用处过滤。 */
  function finish() {
    window.clearTimeout(timer.current);
    setRendering(false);
    onBusyChange(false);
  }
  useEffect(() => {
    if (!version) {
      onBusyChange(false);
      return;
    }
    const token = crypto.randomUUID();
    channel.current = token;
    setReady(false);
    setError("");
    begin();
    function receive(event: MessageEvent) {
      if (
        event.source !== frame.current?.contentWindow ||
        event.data?.channel !== token
      )
        return;
      if (event.data.type === "imv-preview-ready") {
        setReady(true);
        setError("");
      } else if (
        event.data.type === "imv-preview-rendered" &&
        event.data.requestId === requestId.current
      ) {
        finish();
      } else if (
        event.data.type === "imv-preview-error" &&
        (event.data.requestId === undefined ||
          event.data.requestId === requestId.current)
      ) {
        finish();
        setError(
          typeof event.data.message === "string"
            ? event.data.message.slice(0, 160)
            : "预览暂不可用。",
        );
      }
    }
    window.addEventListener("message", receive);
    if (frame.current)
      frame.current.src =
        apiUrl(`/versions/${encodeURIComponent(version.id)}/preview`) +
        "#" +
        token;
    return () => {
      window.clearTimeout(timer.current);
      onBusyChange(false);
      window.removeEventListener("message", receive);
    };
  }, [version?.id, reload, onBusyChange]);
  useEffect(() => {
    if (ready) {
      begin();
      frame.current?.contentWindow?.postMessage(
        {
          type: "imv-preview-update",
          channel: channel.current,
          requestId: ++requestId.current,
          values,
          background,
        },
        "*",
      );
    }
  }, [values, background, ready]);
  /** 背景变更只进入隔离播放器，不写入模板任务。 */
  function load() {
    if (pending || rendering) return;
    try {
      setBackground(backgroundUrl(link));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "视频链接无效");
    }
  }
  const c = version?.spec.composition;
  return (
    <section
      aria-label="实时预览"
      className="flex min-h-0 flex-col rounded-2xl border bg-card p-5 shadow-sm"
    >
      <div className="mb-4 flex items-center gap-2 text-sm font-medium">
        <Film className="size-4" />
        实时预览
        <span className="ml-auto text-xs font-normal text-muted-foreground">
          {c ? `${c.width} × ${c.height} · ${c.fps} fps` : "视频背景 + 字效"}
        </span>
      </div>
      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          load();
        }}
      >
        <Input
          aria-label="背景视频直链"
          placeholder="粘贴背景视频直链（可选）"
          value={link}
          disabled={pending || rendering}
          onChange={(event) => setLink(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={pending || rendering}>
          加载
        </Button>
      </form>
      <div className="relative flex min-h-40 flex-1 items-center justify-center overflow-hidden rounded-xl bg-muted/50">
        {version ? (
          <iframe
            ref={frame}
            title="Remotion 字效播放器"
            sandbox="allow-scripts"
            allow="autoplay; fullscreen"
            allowFullScreen
            referrerPolicy="no-referrer"
            className="size-full min-h-40 border-0"
          />
        ) : (
          <div className="px-6 py-12 text-center">
            <Film className="mx-auto mb-3 size-9 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              你的字效将在这里呈现
            </p>
            <p className="mt-2 text-xs text-muted-foreground/70">
              先在左侧描述想法，再用视频背景查看效果
            </p>
          </div>
        )}
        {(pending || rendering) && !error && (
          <div
            role="status"
            className="absolute inset-0 flex items-center justify-center bg-muted text-sm text-muted-foreground"
          >
            正在渲染预览…
          </div>
        )}
      </div>
      {error && (
        <div className="mt-3 flex items-center justify-between gap-2">
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
          {version && (
            <Button
              variant="ghost"
              size="sm"
              disabled={pending || rendering}
              onClick={() => setReload(reload + 1)}
            >
              <RotateCcw />
              重试预览
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
