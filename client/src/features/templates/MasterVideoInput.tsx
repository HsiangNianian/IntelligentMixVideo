/**
 * 三个预览视频的来源弹窗：每个视频填写 URL 或选择本地文件（只保存在本机，不上传服务端）。
 * 全部读取成功后保存本地文件并一次更新；保存开始前关闭或卸载会取消读取并释放未提交文件的 blob URL，
 * 保存期间不可关闭，保存完成后一定更新来源，使预览与本机存储一致。
 */
import { useEffect, useId, useRef, useState } from "react";
import { FileVideo, FolderOpen, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import type { PreviewMedia } from "./model";
import { previewVideoUrl, readPreviewVideos } from "./media";
import { commitLocalVideos, LOCAL_VIDEO_MAX_BYTES, localVideoSlots, type VideoSource } from "./localVideos";

/** 当前来源中与本地登记一致的槽位显示为文件，其余显示为 URL；尚无来源时按登记或默认地址。 */
function currentSources(media?: PreviewMedia): VideoSource[] {
  const local = localVideoSlots();
  return ([0, 1, 2] as const).map((index) => {
    const video = local[index];
    if (video && (!media || media[index].url === video.url)) return { kind: "file", ...video };
    return { kind: "url", url: media?.[index].url ?? previewVideoUrl(index) };
  });
}

/** 视频编号与来源编号一致；取消不提交来源，加载成功后一次更新三个来源。 */
export function MasterVideoInput({ media, onChange }: { media?: PreviewMedia; onChange: (media: PreviewMedia) => void }) {
  const id = useId();
  const [sources, setSources] = useState(() => currentSources(media));
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const pickers = useRef<(HTMLInputElement | null)[]>([]);
  // 本次弹窗为新选择的文件创建、尚未交给本地登记的 blob URL。
  const pending = useRef(new Set<string>());
  // 本地文件保存中：不能取消，也不能释放即将登记的 blob URL。
  const committing = useRef(false);

  /** 释放未登记的 blob URL；已登记的由本地视频模块在替换或移除时释放。 */
  function release(url?: string) {
    const urls = url ? [url] : [...pending.current];
    const owned = new Set(localVideoSlots().map((video) => video?.url));
    for (const value of urls) {
      if (pending.current.delete(value) && !owned.has(value)) URL.revokeObjectURL(value);
    }
  }
  useEffect(() => () => { controller.current?.abort(); if (!committing.current) release(); }, []);

  /** 打开时读取当前来源，关闭时终止本次请求并丢弃未提交的文件，迟到结果不会修改工作区；保存期间忽略关闭。 */
  function changeOpen(value: boolean) {
    if (committing.current) return;
    controller.current?.abort();
    release();
    setLoading(false);
    setError("");
    if (value) setSources(currentSources(media));
    setOpen(value);
  }

  /** 替换一个槽位的来源，被替换的未提交文件随即释放。 */
  function setSource(index: number, next: VideoSource) {
    const previous = sources[index];
    if (previous.kind === "file" && previous.file) release(previous.url);
    setSources((current) => current.map((source, position) => position === index ? next : source));
  }

  /** 选择本地文件：超过上限直接提示，否则生成 blob URL 等待加载时读取元数据。 */
  function pick(index: number, file: File | undefined) {
    if (!file) return;
    if (file.size > LOCAL_VIDEO_MAX_BYTES) { setError(`视频 ${index + 1}：本地视频不能超过 512 MiB`); return; }
    const url = URL.createObjectURL(file);
    pending.current.add(url);
    setError("");
    setSource(index, { kind: "file", name: file.name, url, file });
  }

  /** 元数据及工作区时间校验均成功、本地文件保存后关闭弹窗；失败时保留输入供修正。 */
  async function load() {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setError("");
    try {
      const media = await readPreviewVideos(sources.map((source) => source.url), request.signal);
      if (request.signal.aborted) return;
      committing.current = true; setSaving(true);
      try { await commitLocalVideos(sources); }
      catch (reason) {
        // 保存中卸载时清理已跳过；保存失败后新选择的 blob URL 不会被登记，此处释放。
        if (request.signal.aborted) release();
        throw reason;
      }
      finally { committing.current = false; setSaving(false); }
      onChange(media);
      changeOpen(false);
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "预览视频读取失败");
    } finally { if (!request.signal.aborted) setLoading(false); }
  }
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button type="button" variant="ghost" size="sm" className="template-master-video-trigger ml-auto h-8 gap-1.5 px-2 text-[11px]"><Upload className="size-3.5" aria-hidden="true" />加载预览视频</Button></DialogTrigger>
    <DialogContent onKeyDown={(event) => { if (event.key === "Enter" && event.target instanceof HTMLInputElement && event.target.type !== "file") { event.preventDefault(); if (!loading) void load(); } }}>
      <DialogHeader><DialogTitle>加载预览视频</DialogTitle><DialogDescription>设置三个视频的 URL 或选择本地文件，URL 视频各使用前 10 秒（不足 10 秒时使用完整视频），本地文件使用完整时长。本地文件只保存在本机、不上传服务器：桌面客户端重启后保留，浏览器刷新后恢复默认。</DialogDescription></DialogHeader>
      <div className="space-y-4">{sources.map((source, index) => <div key={index} className="space-y-2">
        <Label htmlFor={`${id}-${index}`}>{source.kind === "url" ? `视频 ${index + 1} URL` : `视频 ${index + 1} 本地文件`}</Label>
        <div className="flex gap-2">
          {source.kind === "url"
            ? <Input id={`${id}-${index}`} value={source.url} disabled={loading} placeholder="HTTPS 视频直链或 /video.mp4" onChange={(event) => setSource(index, { kind: "url", url: event.target.value })} />
            : <div className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md border px-3 text-sm">
              <FileVideo className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <output id={`${id}-${index}`} className="min-w-0 flex-1 truncate" title={source.name}>{source.name}</output>
              <Button type="button" variant="ghost" size="icon-xs" disabled={loading} aria-label={`移除视频 ${index + 1} 的本地文件`} onClick={() => setSource(index, { kind: "url", url: previewVideoUrl(index as 0 | 1 | 2) })}><X aria-hidden="true" /></Button>
            </div>}
          <input ref={(element) => { pickers.current[index] = element; }} type="file" accept="video/*" aria-label={`视频 ${index + 1} 本地视频文件`} className="sr-only" tabIndex={-1}
            onChange={(event) => { pick(index, event.target.files?.[0]); event.target.value = ""; }} />
          <Button type="button" variant="outline" disabled={loading} aria-label={`为视频 ${index + 1} 选择本地文件`} onClick={() => pickers.current[index]?.click()}><FolderOpen aria-hidden="true" />本地文件</Button>
        </div>
      </div>)}</div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <DialogFooter><Button type="button" variant="outline" disabled={saving} onClick={() => changeOpen(false)}>取消</Button><Button type="button" disabled={loading} onClick={() => void load()}>{loading && <Spinner />}{loading ? "正在读取…" : "加载"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
