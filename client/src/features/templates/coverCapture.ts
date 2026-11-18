/** 只取模板的已合成画布。桌面读取 WebView 内存快照，普通浏览器仅接受用户选中的当前标签页。 */
import { invoke, isTauri } from "@tauri-apps/api/core";

/** 等待播放器定位、渲染或上传前的有界条件，页面隐藏或卸载后不再截取其他画面。 */
export async function waitForCover(test: () => boolean, timeout = 15000, message = "预览未就绪，请重试") {
  const end = performance.now() + timeout;
  while (!test()) {
    if (performance.now() >= end) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

/** 浏览器会话的选择凭证，仅存活到本次保存或重试结束。 */
const captureGrants = new WeakMap<MediaStream, { valid: () => boolean; clear: () => void }>();

/** 停止采集并撤销本次标签页的识别凭证。 */
export function releaseCoverTab(stream?: MediaStream) {
  if (!stream) return;
  stream.getTracks().forEach(track => track.stop());
  captureGrants.get(stream)?.clear();
  captureGrants.delete(stream);
}

/** 浏览器读取当前标签页须由重试点击授权。捕获句柄验证选区属于本页，所有媒体轨道用后即释放。 */
export async function requestCoverTab(): Promise<MediaStream | undefined> {
  if (isTauri()) return;
  const devices = navigator.mediaDevices as MediaDevices & {
    setCaptureHandleConfig?: (options: { handle: string; permittedOrigins: string[] }) => void;
  };
  if (!devices?.setCaptureHandleConfig) throw new Error("请在桌面客户端或支持标签页捕获的浏览器中重试");
  const handle = crypto.randomUUID();
  devices.setCaptureHandleConfig({ handle, permittedOrigins: [location.origin] });
  const clear = () => devices.setCaptureHandleConfig!({ handle: "", permittedOrigins: [] });
  try {
    const stream = await devices.getDisplayMedia({
      video: true, audio: false, preferCurrentTab: true, surfaceSwitching: "exclude",
      selfBrowserSurface: "include", monitorTypeSurfaces: "exclude",
    } as DisplayMediaStreamOptions);
    const track = stream.getVideoTracks()[0] as MediaStreamTrack & { getCaptureHandle?: () => { handle: string } | null };
    const valid = () => track?.readyState === "live" && track.getCaptureHandle?.()?.handle === handle;
    if (!valid()) {
      stream.getTracks().forEach(item => item.stop());
      throw new Error("请在共享选择器中选择当前模板标签页");
    }
    captureGrants.set(stream, { valid, clear });
    return stream;
  } catch (error) {
    clear();
    throw error;
  }
}

/** 从视口截图裁出画面，不包含工具栏、选中框或桌面；只将最终竖屏 PNG 返回上传方。 */
export async function captureFrame(frame: HTMLElement, stream?: MediaStream): Promise<Blob> {
  const viewport = { width: innerWidth, height: innerHeight };
  const offscreen = Boolean(frame.closest(".home-cover-capture[data-offscreen]"));
  const extent = offscreen ? { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight } : viewport;
  const rect = frame.getBoundingClientRect();
  let source: HTMLImageElement | HTMLVideoElement;
  let url: string | undefined;
  try {
    if (stream) {
      const video = document.createElement("video");
      video.muted = true;
      video.srcObject = stream;
      await video.play();
      await waitForCover(() => video.readyState >= 2);
      // 等标签页采集推进到本次定位后的合成画面。
      const start = video.currentTime;
      await waitForCover(() => video.currentTime > start + 0.12);
      source = video;
    } else {
      if (!isTauri()) throw new Error("请点击重试并选择当前标签页");
      const png = await invoke<ArrayBuffer>("preview_snapshot", offscreen ? { documentSize: [extent.width, extent.height] } : {});
      url = URL.createObjectURL(new Blob([png], { type: "image/png" }));
      source = new Image();
      source.src = url;
      await source.decode();
    }
    const width = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
    const height = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
    const box = frame.getBoundingClientRect();
    if (innerWidth !== viewport.width || innerHeight !== viewport.height || !frame.isConnected || !rect.width || !rect.height
      || Math.abs(box.left - rect.left) > 1 || Math.abs(box.top - rect.top) > 1 || Math.abs(box.width - rect.width) > 1
      || rect.left < 0 || rect.top < 0 || rect.right > extent.width + 1 || rect.bottom > extent.height + 1
      || !offscreen && [[0.5, 0.5], [0.01, 0.01], [0.99, 0.01], [0.01, 0.99], [0.99, 0.99]].some(([x, y]) =>
        !frame.contains(document.elementFromPoint(rect.left + rect.width * x, rect.top + rect.height * y))))
      throw new Error("预览画布位置已变化，请重试");
    if (stream && !captureGrants.get(stream)?.valid()) throw new Error("标签页共享已变化，请重试");
    const canvas = document.createElement("canvas");
    canvas.width = 432; canvas.height = 768;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("画布不可用");
    context.drawImage(source, rect.x * width / extent.width, rect.y * height / extent.height,
      rect.width * width / extent.width, rect.height * height / extent.height, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("截图失败")), "image/png"));
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/** 各字效按片段内的相对时间定位。等本次取帧和最新参数都画完，才能截取合成画面。 */
function coverLayersReady(frame: HTMLElement, captureId: string): boolean {
  const overlays = [...frame.querySelectorAll<HTMLIFrameElement>("iframe[sandbox]")];
  if (overlays.some(layer => layer.style.visibility === "visible" && layer.dataset.coverAt === undefined)) return false;
  const layers = overlays.filter(layer => layer.dataset.coverAt !== undefined);
  if (layers.length !== Number(frame.parentElement?.dataset.coverLayers)) return false;
  let ready = true;
  for (const layer of layers) {
    const data = layer.dataset;
    if (data.coverSeekSupported === "false") throw new Error("字效预览包不支持封面定位，请重新发布资产后重试");
    if (layer.style.visibility !== "visible" || data.coverPainted !== "true") { ready = false; continue; }
    const id = `${captureId}:${data.coverRequest}`;
    if (data.coverSeek !== id) {
      data.coverSeek = id;
      layer.contentWindow?.postMessage({ type: "imv-preview-sync", channel: new URL(layer.src).hash.slice(1),
        time: Number(data.coverAt), duration: Number(data.coverDuration), captureId: id }, "*");
    }
    if (data.coverSeekPainted !== id) ready = false;
  }
  return ready;
}

/** SDK 5.2.2 在独立字幕画布上每帧先清空，再为每个已渲染的 IMS 文字叠一张位图。canplay 早于字体下载和绘制。 */
export async function prepareCoverFrame(frame: HTMLElement, seek: (seconds: number) => void) {
  const stage = frame.parentElement!;
  const ready = () => {
    if (!stage.isConnected) throw new Error("取帧已取消");
    return stage.dataset.coverReady === "true";
  };
  await waitForCover(ready, 60000);
  // 已经暂停在 1 秒时 SDK 会跳过重复定位；先离开该帧，确保能观察到本次重绘。
  if (stage.dataset.coverTime === "1") {
    seek(0);
    await waitForCover(() => ready() && stage.dataset.coverTime === "0");
  }
  const expected = Number(stage.dataset.coverTextLayers);
  const doc = frame.querySelector<HTMLIFrameElement>(".template-preview-player iframe")?.contentDocument;
  const ctx = doc?.querySelector<HTMLCanvasElement>("#subtitle-canvas")?.getContext("2d");
  if (expected && !ctx) throw new Error("无法确认预览文字图层，请刷新预览后重试");
  let painted = 0;
  const clear = ctx?.clearRect, draw = ctx?.drawImage;
  try {
    if (expected && ctx && clear && draw) {
      ctx.clearRect = (...args) => { clear.apply(ctx, args); painted = 0; };
      ctx.drawImage = (image, ...coordinates: number[]) => { Reflect.apply(draw, ctx, [image, ...coordinates]); painted++; };
    }
    seek(1);
    const captureId = crypto.randomUUID();
    const complete = () => ready() && stage.dataset.coverTime === "1" && painted >= expected && coverLayersReady(frame, captureId);
    await waitForCover(complete, 60000, "1 秒处的标题、字幕或字效未绘制完成，请刷新预览后重试");
    await new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done())));
    if (!complete()) throw new Error("预览已变化，请重试");
  } finally {
    if (expected && ctx && clear && draw) { ctx.clearRect = clear; ctx.drawImage = draw; }
  }
}
