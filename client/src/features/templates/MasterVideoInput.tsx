/** 三个预览视频的地址弹窗；全部读取成功后更新，关闭或卸载时取消读取并释放资源。 */
import { useEffect, useId, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import type { PreviewMedia } from "./model";
import { previewUrls, readPreviewVideos } from "./media";

/** 视频编号与来源编号一致；取消不提交地址，加载成功后一次更新三个来源。 */
export function MasterVideoInput({ media, onChange }: { media?: PreviewMedia; onChange: (media: PreviewMedia) => void }) {
  const id = useId();
  const [urls, setUrls] = useState(() => previewUrls(media));
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  /** 打开时读取当前来源，关闭时终止本次请求，迟到结果不会修改工作区。 */
  function changeOpen(value: boolean) {
    controller.current?.abort();
    setLoading(false);
    setError("");
    if (value) setUrls(previewUrls(media));
    setOpen(value);
  }

  /** 元数据及工作区时间校验均成功后关闭弹窗；失败时保留输入供修正。 */
  async function load() {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setError("");
    try {
      const media = await readPreviewVideos(urls, request.signal);
      if (request.signal.aborted) return;
      onChange(media);
      changeOpen(false);
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "预览视频读取失败");
    } finally { if (!request.signal.aborted) setLoading(false); }
  }
  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogTrigger asChild><Button type="button" variant="ghost" size="sm" className="template-master-video-trigger ml-auto h-8 gap-1.5 px-2 text-[11px]"><Upload className="size-3.5" aria-hidden="true" />加载预览视频</Button></DialogTrigger>
    <DialogContent onKeyDown={(event) => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) { event.preventDefault(); if (!loading) void load(); } }}>
      <DialogHeader><DialogTitle>加载预览视频</DialogTitle><DialogDescription>设置三个视频的 URL，各使用前 10 秒，不足 10 秒时使用完整视频。</DialogDescription></DialogHeader>
      <div className="space-y-4">{urls.map((url, index) => <div key={index} className="space-y-2">
        <Label htmlFor={`${id}-${index}`}>视频 {index + 1} URL</Label>
        <Input id={`${id}-${index}`} value={url} disabled={loading} placeholder="HTTPS 视频直链或 /video.mp4" onChange={(event) => setUrls((current) => current.map((value, position) => position === index ? event.target.value : value))} />
      </div>)}</div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <DialogFooter><Button type="button" variant="outline" onClick={() => changeOpen(false)}>取消</Button><Button type="button" disabled={loading} onClick={() => void load()}>{loading && <Spinner />}{loading ? "正在读取…" : "加载"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
