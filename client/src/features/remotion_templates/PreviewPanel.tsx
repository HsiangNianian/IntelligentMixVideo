/**
 * 隔离预览容器；仅向当前成功版本的 iframe 发送参数，背景视频在客户端底层独立播放，
 * 清理消息监听和就绪超时。带关键词字效的字效在未填关键词时以示例关键词预览（见 `sampleKeyword`），参数本身不变。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Film, FolderOpen, Maximize, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { commitLocalVideos, LOCAL_VIDEO_MAX_BYTES, localPreviewVideos, localVideoSlots, subscribeLocalVideos } from "@/features/templates/localVideos";
import { readMasterVideo } from "@/features/templates/media";
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
  const [useLocal, setUseLocal] = useState(true);
  const [localVideo, setLocalVideo] = useState(() => localVideoSlots()[0]);
  const [loadingFile, setLoadingFile] = useState(false);
  const [backgroundError, setBackgroundError] = useState("");
  const videoUrl = useLocal ? localVideo?.url ?? "" : background;
  const [error, setError] = useState("");
  // 已就绪的通道而不是布尔值：切换版本时 iframe 若在清空渲染前就回报就绪，false→true 会被合并成未变化，
  // 参数不再下发，预览锁到超时；每次加载的通道都不同，就绪必然触发一次下发。
  const [ready, setReady] = useState("");
  const [rendering, setRendering] = useState(false);
  const [reload, setReload] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const fileRequest = useRef<AbortController | null>(null);
  const channel = useRef("");
  const requestId = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const blocking = useRef(false);
  const rendered = useRef(false);
  useEffect(() => {
    let active = true;
    const refresh = () => setLocalVideo(localVideoSlots()[0]);
    const unsubscribe = subscribeLocalVideos(refresh);
    void localPreviewVideos().then(() => { if (active) refresh(); }).catch((reason) => {
      if (active) setBackgroundError(reason instanceof Error ? reason.message : "本地视频读取失败");
    });
    return () => { active = false; unsubscribe(); fileRequest.current?.abort(); };
  }, []);
  useEffect(() => { setBackgroundError(""); }, [videoUrl]);
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
          background: "",
        },
        "*",
      );
    }
  }, [shown, ready]);
  /** 只校验所选文件并替换共享视频 1；失败保留旧视频，未登记的 URL 及时释放。 */
  async function pick(file: File | undefined) {
    if (!file || fileRequest.current) return;
    if (file.size > LOCAL_VIDEO_MAX_BYTES) { setBackgroundError("本地视频不能超过 512 MiB"); return; }
    const request = new AbortController();
    fileRequest.current = request;
    const url = URL.createObjectURL(file);
    setLoadingFile(true);
    setBackgroundError("");
    try {
      const media = await readMasterVideo(url, request.signal);
      if (request.signal.aborted) return;
      await commitLocalVideos([{ kind: "file", name: file.name, url, file }], [media]);
      if (!request.signal.aborted) { setUseLocal(true); setBackground(""); }
    } catch (reason) {
      if (!request.signal.aborted) setBackgroundError(reason instanceof Error ? reason.message : "本地视频读取失败");
    } finally {
      if (localVideoSlots()[0]?.url !== url) URL.revokeObjectURL(url);
      fileRequest.current = null;
      if (!request.signal.aborted) setLoadingFile(false);
    }
  }
  /** 直链与本地文件都只更新客户端底层视频。 */
  function load() {
    if (pending || rendering || loadingFile) return;
    try {
      setBackground(backgroundUrl(link));
      setUseLocal(false);
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
        {(version || videoUrl) && <Button type="button" variant="ghost" size="icon-sm" aria-label="全屏预览"
          onClick={() => { void stage.current?.requestFullscreen().catch(() => setBackgroundError("当前环境无法进入全屏预览。")); }}>
          <Maximize aria-hidden="true" />
        </Button>}
      </div>
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <input ref={picker} type="file" accept="video/*" aria-label="本地预览视频文件" className="sr-only" tabIndex={-1}
          onChange={(event) => { void pick(event.target.files?.[0]); event.target.value = ""; }} />
        <Button type="button" variant="outline" disabled={loadingFile} onClick={() => picker.current?.click()}>
          {loadingFile ? <Spinner /> : <FolderOpen aria-hidden="true" />}{loadingFile ? "正在加载…" : "加载本地文件"}
        </Button>
        {useLocal && localVideo && <span className="truncate text-xs text-muted-foreground" title={localVideo.name}>{localVideo.name}</span>}
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
          disabled={pending || rendering || loadingFile}
          onChange={(event) => setLink(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={pending || rendering || loadingFile}>
          加载
        </Button>
      </form>
      <div
        ref={stage}
        className="relative flex min-h-40 flex-1 items-center justify-center overflow-hidden rounded-lg border border-dashed bg-muted/40"
        style={{ containerType: "size" }}
      >
        {version || videoUrl ? (
          <div className="relative" style={{ width: `min(100cqw, ${100 * aspect}cqh)`, aspectRatio: aspect,
            backgroundColor: "#25252b", backgroundImage: "conic-gradient(#35353d 25%, transparent 0 50%, #35353d 0 75%, transparent 0)", backgroundSize: "24px 24px" }}>
            {videoUrl && <video key={videoUrl} aria-label="预览背景视频" src={videoUrl} autoPlay muted loop playsInline
              className="absolute inset-0 h-full w-full object-cover"
              onError={() => setBackgroundError("背景视频无法播放，请检查文件格式或视频直链。")}
            />}
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
