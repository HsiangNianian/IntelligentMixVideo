/** 预览视频输入读取媒体信息；取消或卸载时释放视频元素和监听器。 */
import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { MasterVideo } from "./model";
import { readMasterVideo } from "./media";

/** 读取成功后仅替换工作区的预览视频，模板规则保持不变。 */
export function MasterVideoInput({ media, onChange }: { media?: MasterVideo; onChange: (media: MasterVideo) => void }) {
  const id = useId();
  const [url, setUrl] = useState(media?.url ?? import.meta.env.VITE_PREVIEW_VIDEO_URL?.trim() ?? "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { controller.current?.abort(); setLoading(false); setError(""); setUrl(media?.url ?? import.meta.env.VITE_PREVIEW_VIDEO_URL?.trim() ?? ""); }, [media]);
  useEffect(() => () => controller.current?.abort(), []);
  /** 每次读取独立取消信号，迟到响应不能覆盖新选择。 */
  async function load() {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setError("");
    try {
      if (!url.trim()) throw new Error("请输入母版视频地址");
      const media = await readMasterVideo(url.trim(), request.signal);
      if (request.signal.aborted) return;
      onChange(media);
    } catch (reason) {
      if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "母版读取失败");
    } finally { if (!request.signal.aborted) setLoading(false); }
  }
  return <section className="space-y-2 border-b p-4" aria-label="预览视频">
    <Label htmlFor={id}>预览视频地址</Label>
    <div className="flex gap-2"><Input id={id} value={url} placeholder="HTTPS 视频直链或 /video.mp4" onChange={(event) => setUrl(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void load(); } }} /><Button type="button" variant="outline" disabled={loading} onClick={() => void load()}>{loading ? "正在读取…" : "加载预览视频"}</Button></div>
    {media ? <p className="text-xs text-muted-foreground">{media.width} × {media.height} · {media.duration.toFixed(2)} 秒</p> : <p className="text-xs text-muted-foreground">当前使用十秒示例。选择视频后按实际时长计算对象时间，预览视频独立于模板保存。</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </section>;
}
