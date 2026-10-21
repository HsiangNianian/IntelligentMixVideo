/**
 * 调试日志：桌面客户端以 `--debug` 启动时，把页面报错与模板预览的诊断信息转发到原生终端；
 * 浏览器或未带参数时所有函数都是空操作。监听主窗口及同源 iframe（模板预览 SDK）的未捕获错误、
 * 未处理的 Promise 拒绝、资源加载失败与 console.error/warn；连续相同的记录只发一次，之后汇报重复次数。
 */
import { invoke, isTauri } from "@tauri-apps/api/core";

type Level = "错误" | "警告" | "信息";

/** 单条记录的字符上限，避免超长源码或数据经 IPC 刷屏。 */
const MAX_LENGTH = 8000;
/** 媒体错误码（MediaError.code）的中文含义。 */
const MEDIA_ERRORS: Record<number, string> = { 1: "加载被中止", 2: "网络错误", 3: "解码失败", 4: "格式或地址不受支持" };
/** 启动时报告的解码能力：桌面 WebView 是否能播放常见编码，定位“视频无法播放”时先看这一行。 */
const CODECS: [string, string][] = [
  ["H.264", 'video/mp4; codecs="avc1.42E01E"'],
  ["H.265", 'video/mp4; codecs="hvc1.1.6.L93.B0"'],
  ["VP9", 'video/webm; codecs="vp9"'],
  ["AV1", 'video/mp4; codecs="av01.0.05M.08"'],
];

let starting: Promise<void> | undefined;
let enabled = false;
let last = { key: "", level: "错误" as Level, source: "", repeats: 0 };
let repeatTimer: ReturnType<typeof setInterval> | undefined;
let stopMain: (() => void) | undefined;
const watched = new WeakSet<Window>();

/** 交给原生层输出；失败时静默，不能再写 console，否则会被自己的监听再次转发。 */
function send(level: Level, source: string, message: string) {
  void invoke("diagnostics_log", { level, source, message: message.slice(0, MAX_LENGTH) }).catch(() => {});
}

/** 汇报上一条记录被省略的重复次数。 */
function reportRepeats() {
  if (!last.repeats) return;
  send(last.level, last.source, `上一条又出现了 ${last.repeats} 次`);
  last.repeats = 0;
}

/** 记录一条诊断信息；未开启调试日志时什么都不做。连续相同的记录只发送第一次，重复次数每 2 秒汇报一次。 */
export function debugLog(source: string, message: string, level: Level = "错误") {
  if (!enabled) return;
  const key = `${level}\n${source}\n${message}`;
  if (key === last.key) { last.repeats++; return; }
  reportRepeats();
  last = { key, level, source, repeats: 0 };
  send(level, source, message);
}

/** 把任意值格式化为文本：错误带名称与调用栈（跨 iframe 的错误同样识别），其他对象尽量序列化。 */
export function describe(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "message" in value && typeof value.message === "string") {
    const head = `${"name" in value && typeof value.name === "string" ? value.name : "Error"}: ${value.message}`;
    const stack = "stack" in value && typeof value.stack === "string" ? value.stack : "";
    // V8 的调用栈以“名称: 消息”开头，WebKit 的只有调用位置。
    return !stack ? head : stack.startsWith(head) ? stack : `${head}\n${stack}`;
  }
  try { return JSON.stringify(value) ?? String(value); } catch { return String(value); }
}

/** 媒体元素的地址、错误码与加载状态；报错时仍保留在元素上，须在清除地址之前读取。 */
export function describeMedia(media: HTMLMediaElement): string {
  const error = media.error;
  const reason = error ? `${error.code}（${MEDIA_ERRORS[error.code] ?? "未知"}）${error.message ? ` ${error.message}` : ""}` : "无";
  return `地址 ${media.currentSrc || media.src || "（无）"}；媒体错误 ${reason}；networkState=${media.networkState} readyState=${media.readyState}`;
}

/**
 * 监听一个页面：未捕获错误、未处理的 Promise 拒绝、捕获阶段的资源加载失败（图片、脚本、页面里的视频）与 console.error/warn。
 * 同一窗口只监听一次；iframe 随页面销毁时监听一并消失。返回的函数撤销监听并恢复 console。
 */
export function watchWindow(target: Window, source: string): () => void {
  if (!enabled || watched.has(target)) return () => {};
  watched.add(target);
  const scope = target as Window & typeof globalThis;
  const onError = (event: Event) => {
    const element = event.target as Element | null;
    if (element && element !== (target as unknown) && "tagName" in element) {
      const detail = "error" in element && "networkState" in element ? describeMedia(element as HTMLMediaElement) : `地址 ${element.getAttribute("src") ?? element.getAttribute("href") ?? "（无）"}`;
      debugLog(source, `${element.tagName.toLowerCase()} 加载失败；${detail}`, "警告");
      return;
    }
    const error = event as ErrorEvent;
    debugLog(source, `${error.error ? describe(error.error) : error.message}${error.filename ? `\n  位于 ${error.filename}:${error.lineno}:${error.colno}` : ""}`);
  };
  const onRejection = (event: PromiseRejectionEvent) => debugLog(source, `未处理的 Promise 拒绝：${describe(event.reason)}`);
  target.addEventListener("error", onError, true);
  target.addEventListener("unhandledrejection", onRejection);
  const originals = { error: scope.console.error, warn: scope.console.warn };
  for (const [method, level] of [["error", "错误"], ["warn", "警告"]] as const) {
    scope.console[method] = (...args: unknown[]) => {
      originals[method].apply(scope.console, args);
      debugLog(source, args.map(describe).join(" "), level);
    };
  }
  return () => {
    target.removeEventListener("error", onError, true);
    target.removeEventListener("unhandledrejection", onRejection);
    Object.assign(scope.console, originals);
    watched.delete(target);
  };
}

/** 启动时询问原生层一次；开启后监听主窗口，并报告浏览器标识与常见编码的解码能力。 */
export function startDiagnostics(): Promise<void> {
  if (!isTauri()) return Promise.resolve();
  starting ??= invoke<boolean>("diagnostics_enabled").catch(() => false).then((value) => {
    if (!value) return;
    enabled = true;
    repeatTimer = setInterval(reportRepeats, 2000);
    stopMain = watchWindow(window, "页面");
    const probe = document.createElement("video");
    debugLog("环境", `${navigator.userAgent}\n  解码能力 ${CODECS.map(([name, type]) => `${name}=${probe.canPlayType(type) || "不支持"}`).join(" ")}`, "信息");
  });
  return starting;
}

/** 测试之间恢复初始状态：撤销主窗口监听并停止重复计数。 */
export function resetDiagnostics() {
  stopMain?.();
  clearInterval(repeatTimer);
  starting = stopMain = repeatTimer = undefined;
  enabled = false;
  last = { key: "", level: "错误", source: "", repeats: 0 };
}
