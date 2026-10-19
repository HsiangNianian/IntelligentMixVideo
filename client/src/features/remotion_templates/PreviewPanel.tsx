/**
 * 隔离预览容器；仅向当前成功版本的 iframe 发送参数，直链或本地背景视频在客户端底层独立播放，
 * 清理消息监听和就绪超时。带关键词字效的字效在未填关键词时以示例关键词预览（见 `sampleKeyword`），参数本身不变。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Film, FolderOpen, Maximize, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiUrl } from "./api";
import { backgroundUrl, sampleKeyword, type Values, type Version } from "./model";
import { Spinner } from "@/components/ui/spinner";

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
  const sample = sampleKeyword(version, values);
  const shown = useMemo(() => (sample ? { ...values, keywords: [sample] } : values), [values, sample]);
  const [link, setLink] = useState("");
  const [background, setBackground] = useState("");
  const [backgroundError, setBackgroundError] = useState("");
  // 参与背景视频的 key：重新加载同一直链也会重建视频元素，失败后可以重试。
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  // 已就绪的通道而不是布尔值：切换版本时 iframe 若在清空渲染前就回报就绪，false→true 会被合并成未变化，
  // 参数不再下发，预览锁到超时；每次加载的通道都不同，就绪必然触发一次下发。
  const [ready, setReady] = useState("");
  const [rendering, setRendering] = useState(false);
  const [reload, setReload] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const channel = useRef("");
  const requestId = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const blocking = useRef(false);
  const rendered = useRef(false);
  // 本地文件的 blob URL 在更换背景或卸载时释放。
  useEffect(() => () => {
    if (background.startsWith("blob:")) URL.revokeObjectURL(background);
  }, [background]);
  /** 每次加载都有超时出口；播放本身不触发锁定。 */
  function begin(lock = true) {
    window.clearTimeout(timer.current);
    blocking.current = lock;
    setRendering(lock);
    onBusyChange(lock);
    timer.current = window.setTimeout(() => {
      setError("预览加载超时，请检查服务连接后重试。");
      setRendering(false);
      blocking.current = false;
      onBusyChange(false);
    }, 20_000);
  }
  /** 完成或失败都释放操作锁，旧请求的完成消息由调用处过滤。 */
  function finish() {
    window.clearTimeout(timer.current);
    blocking.current = false;
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
    rendered.current = false;
    setReady("");
    setError("");
    begin();
    function receive(event: MessageEvent) {
      if (
        event.source !== frame.current?.contentWindow ||
        event.data?.channel !== token
      )
        return;
      if (event.data.type === "imv-preview-ready") {
        setReady(token);
        setError("");
      } else if (
        event.data.type === "imv-preview-rendered" &&
        event.data.requestId === requestId.current
      ) {
        rendered.current = true;
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
        apiUrl(`/versions/${encodeURIComponent(version.id)}/preview?overlay=true`) +
        "#" +
        token;
    return () => {
      window.clearTimeout(timer.current);
      onBusyChange(false);
      window.removeEventListener("message", receive);
    };
  }, [version?.id, reload, onBusyChange]);
  useEffect(() => {
    if (ready && ready === channel.current) {
      // 参数草稿更新不遮挡画面或锁控件，背景视频不参与字效的就绪握手。
      begin(blocking.current || !rendered.current);
      setError("");
      frame.current?.contentWindow?.postMessage(
        {
          type: "imv-preview-update",
          channel: channel.current,
          requestId: ++requestId.current,
          values: shown,
          // 已封存的播放器包要求该字段为字符串；背景改由父页面播放，始终传空。
          background: "",
        },
        "*",
      );
    }
  }, [shown, ready]);
  /** 本地文件只在页面内以 blob URL 播放，不上传、不保存。 */
  function pick(file: File | undefined) {
    if (!file) return;
    setBackground(URL.createObjectURL(file));
    setBackgroundError("");
  }
  /** 直链只更新客户端底层视频，不进入 iframe 或生成请求。 */
  function load() {
    try {
      setBackground(backgroundUrl(link));
      setAttempt((value) => value + 1);
      setBackgroundError("");
    } catch (reason) {
      setBackgroundError(reason instanceof Error ? reason.message : "视频链接无效");
    }
  }
  const c = version?.spec.composition;
  const aspect = c ? c.width / c.height : 9 / 16;
  return (
    <section
      aria-label="实时预览"
      className="flex min-h-0 min-w-0 flex-col rounded-xl border bg-card p-4"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm font-medium">
        <Film className="size-4" />
        实时预览
        {version && (
          <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-primary">
            V{version.number}
          </span>
        )}
        <span className="ml-auto text-xs font-normal text-muted-foreground">
          {c ? `${c.width} × ${c.height} · ${c.fps} fps` : "视频背景 + 字效"}
        </span>
        {/* 全屏整个画布，背景视频与字效一起显示；iframe 自身的全屏只含字效，因此禁用。 */}
        {(version || background) && <Button type="button" variant="ghost" size="icon-sm" aria-label="全屏预览"
          onClick={() => void stage.current?.requestFullscreen()}>
          <Maximize aria-hidden="true" />
        </Button>}
      </div>
      <div className="mb-2">
        <input ref={picker} type="file" accept="video/*" aria-label="本地预览视频文件" className="sr-only" tabIndex={-1}
          onChange={(event) => { pick(event.target.files?.[0]); event.target.value = ""; }} />
        <Button type="button" variant="outline" onClick={() => picker.current?.click()}>
          <FolderOpen aria-hidden="true" />加载本地文件
        </Button>
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
          onChange={(event) => setLink(event.target.value)}
        />
        <Button type="submit" variant="outline">
          加载
        </Button>
      </form>
      <div
        ref={stage}
        className="relative flex min-h-40 flex-1 items-center justify-center overflow-hidden rounded-lg border border-dashed bg-muted/40"
        style={{ containerType: "size" }}
      >
        {version || background ? (
          // 按画布比例排布，背景视频与透明播放器对齐；画布与模板预览一样为黑底。
          <div className="relative bg-black" style={{ width: `min(100cqw, ${100 * aspect}cqh)`, aspectRatio: aspect }}>
            {/* 先读元数据再播放，避免 WebKitGTK 直接自动播放较大 blob 时的解码错误。 */}
            {background && <video key={`${attempt}:${background}`} aria-label="预览背景视频" src={background} preload="metadata" muted loop playsInline
              className="absolute inset-0 size-full object-cover"
              onLoadedMetadata={(event) => void event.currentTarget.play().catch(() => {})}
              onError={() => setBackgroundError("背景视频无法播放，请检查文件格式或视频直链。")}
            />}
            {/* 与透明页面的 color-scheme 一致，否则深色主题下浏览器会把 iframe 画成不透明。 */}
            {version && <iframe
              ref={frame}
              title="Remotion 字效播放器"
              sandbox="allow-scripts"
              allow="autoplay; fullscreen 'none'"
              referrerPolicy="no-referrer"
              className="absolute inset-0 h-full w-full border-0"
              style={{ colorScheme: "light" }}
            />}
          </div>
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
        {rendering && !error && (
          <div
            role="status"
            className="absolute inset-0 flex items-center justify-center gap-2 bg-muted text-sm text-muted-foreground"
          >
            <Spinner />
            正在渲染预览…
          </div>
        )}
      </div>
      {sample && (
        <p className="mt-2 text-xs text-muted-foreground">
          预览以「{sample}」为示例关键词展示关键词字效；模板里由切片关键词决定，只能开关六个关键词样式。
        </p>
      )}
      {backgroundError && <p role="alert" className="mt-3 text-xs text-destructive">{backgroundError}</p>}
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
