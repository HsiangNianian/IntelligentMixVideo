/**
 * 调试日志：桌面客户端持续在主窗口与模板预览 iframe 中记录错误、资源失败和控件操作；
 * 日志先保存在有界内存中，用户在设置里打开日志窗口后转发到原生终端日志文件。
 * 浏览器不开启收集；相同记录合并，密码、密钥等敏感输入只记录“已隐藏”。
 */
import { invoke, isTauri } from "@tauri-apps/api/core";

/** 日志级别与原生窗口的显示格式一致。 */
export type DiagnosticLevel = "错误" | "警告" | "信息";

/** 一条待转发记录；消息在进入 IPC 前统一截断。 */
type Entry = { level: DiagnosticLevel; source: string; message: string };

/** 单条记录、内存缓冲与控件标签的上限，避免日志窗口和 IPC 被长文本刷满。 */
const MAX_LENGTH = 8000;
const MAX_BUFFER_ENTRIES = 400;
const MAX_BUFFER_CHARS = 200_000;
const MAX_CONTROL_LABEL = 100;

/** 媒体错误码（MediaError.code）的中文含义。 */
const MEDIA_ERRORS: Record<number, string> = { 1: "加载被中止", 2: "网络错误", 3: "解码失败", 4: "格式或地址不受支持" };
/** 启动时报告的解码能力：桌面 WebView 是否能播放常见编码，定位“视频无法播放”时先看这一行。 */
const CODECS: [string, string][] = [
  ["H.264", 'video/mp4; codecs="avc1.42E01E"'],
  ["H.265", 'video/mp4; codecs="hvc1.1.6.L93.B0"'],
  ["VP9", 'video/webm; codecs="vp9"'],
  ["AV1", 'video/mp4; codecs="av01.0.05M.08"'],
];
/** 统一识别页面控件；点击其子元素时仍按外层控件记录。 */
const CONTROLS = "button, a, input, select, textarea, summary, [contenteditable='true'], [role='button'], [role='tab'], [role='checkbox'], [role='switch'], [role='slider'], [role='textbox'], [role='menuitem']";
/** 名称、类型或自动填充提示包含这些词时，不把值写进日志。 */
const SENSITIVE = /password|passwd|secret|token|api.?key|access.?key|credential|密钥|密码|凭据|授权|私钥/i;

let started = false;
let enabled = false;
let stopMain: (() => void) | undefined;
let repeatTimer: ReturnType<typeof setInterval> | undefined;
let buffer: Entry[] = [];
let bufferChars = 0;
let last = { key: "", level: "错误" as DiagnosticLevel, source: "", repeats: 0 };
const watched = new WeakSet<Window>();

/** 交给原生层输出；失败时静默，不能再写 console，否则会被自己的监听再次转发。 */
function send(level: DiagnosticLevel, source: string, message: string) {
  void invoke("diagnostics_log", { level, source, message: message.slice(0, MAX_LENGTH) }).catch(() => {});
}

/** 把尚未打开窗口时的记录放入有界缓冲；只保留最近内容，避免长期运行吃满内存。 */
function remember(entry: Entry) {
  buffer.push(entry);
  bufferChars += entry.message.length;
  while (buffer.length > MAX_BUFFER_ENTRIES || bufferChars > MAX_BUFFER_CHARS) {
    bufferChars -= buffer.shift()?.message.length ?? 0;
  }
}

/** 打开日志窗口后按发生顺序发送缓冲，随后清空，避免再次打开时重复旧记录。 */
function flushBuffer() {
  const entries = buffer;
  buffer = [];
  bufferChars = 0;
  for (const entry of entries) send(entry.level, entry.source, entry.message);
}

/** 汇报上一条记录被省略的重复次数。 */
function reportRepeats() {
  if (!enabled || !last.repeats) return;
  send(last.level, last.source, `上一条又出现了 ${last.repeats} 次`);
  last.repeats = 0;
}

/** 记录一条诊断信息；窗口打开前进入内存缓冲，打开后直接转发。连续相同的记录只发送第一次。 */
export function debugLog(source: string, message: string, level: DiagnosticLevel = "错误") {
  if (!started) return;
  const clipped = message.slice(0, MAX_LENGTH);
  const key = `${level}\n${source}\n${clipped}`;
  if (key === last.key) {
    last.repeats++;
    return;
  }
  reportRepeats();
  last = { key, level, source, repeats: 0 };
  if (enabled) send(level, source, clipped);
  else remember({ level, source, message: clipped });
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

/** 将空白和多行压成一行，避免控件名称破坏日志的单条记录格式。 */
function oneLine(value: string, limit = MAX_CONTROL_LABEL): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/** 查找事件目标对应的控件；点击按钮内部的图标或文字时向上归到按钮。 */
function controlTarget(target: EventTarget | null): Element | null {
  return target && typeof target === "object" && "closest" in target
    ? (target as Element).closest(CONTROLS)
    : null;
}

/** 读取控件名称：优先无障碍名称，其次关联标签、占位符和可见文字，最后退回 name/id。 */
function controlLabel(control: Element): string {
  const explicit = control.getAttribute("aria-label") || control.getAttribute("title");
  if (explicit) return oneLine(explicit);
  const labelled = control as Element & { labels?: ArrayLike<HTMLLabelElement> };
  const associated = labelled.labels ? Array.from(labelled.labels).map((label) => label.textContent ?? "").join(" ") : "";
  const ownText = control.tagName === "INPUT" && ["button", "submit", "reset"].includes((control as HTMLInputElement).type)
    ? (control as HTMLInputElement).value
    : control.textContent ?? "";
  return oneLine(associated || (control as HTMLInputElement).placeholder || control.getAttribute("aria-labelledby") || ownText || control.getAttribute("name") || control.id || control.tagName.toLowerCase());
}

/** 判断输入是否可能包含凭据；密码输入以及常见密钥字段统一隐藏值。 */
function sensitive(control: Element, label: string): boolean {
  if (control.tagName === "INPUT" && (control as HTMLInputElement).type === "password") return true;
  return SENSITIVE.test(`${label} ${control.getAttribute("name") ?? ""} ${control.getAttribute("id") ?? ""} ${control.getAttribute("autocomplete") ?? ""}`);
}

/** 描述控件变化：勾选、选项和普通值可用时记录，敏感值只显示已隐藏；文本值限制长度。 */
function controlValue(control: Element, label: string): string {
  if (control.tagName === "INPUT") {
    const input = control as HTMLInputElement;
    if (input.type === "file") return `选择 ${input.files?.length ?? 0} 个文件`;
    if (sensitive(control, label)) return "值已隐藏";
    if (input.type === "checkbox" || input.type === "radio") return input.checked ? "选中" : "未选中";
    if (!input.value) return "已清空";
    return `值「${oneLine(input.value, 160)}」`;
  }
  if (control.tagName === "SELECT") {
    const select = control as HTMLSelectElement;
    return `选项「${oneLine(select.selectedOptions[0]?.textContent || select.value || "（空）", 160)}」`;
  }
  if (control.tagName === "TEXTAREA") {
    const textarea = control as HTMLTextAreaElement;
    return sensitive(control, label)
      ? "值已隐藏"
      : textarea.value ? `值「${oneLine(textarea.value, 160)}」` : "已清空";
  }
  return "";
}

/** 描述控件本身，供点击、修改和表单提交共用；不输出密码或密钥内容。 */
export function describeControl(control: Element): string {
  const tag = control.tagName.toLowerCase();
  const role = control.getAttribute("role");
  const inputType = control instanceof HTMLInputElement ? control.type : "";
  const kind = role || (inputType ? `${tag}[${inputType}]` : tag);
  return `${kind}「${controlLabel(control)}」`;
}

/**
 * 监听一个页面：未捕获错误、未处理的 Promise 拒绝、捕获阶段的资源加载失败（图片、脚本、页面里的视频），
 * console.error/warn，以及点击、修改、提交等控件操作。同一窗口只监听一次；返回的函数撤销监听并恢复 console。
 */
export function watchWindow(target: Window, source: string): () => void {
  if (watched.has(target)) return () => {};
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
  const onClick = (event: Event) => {
    const control = controlTarget(event.target);
    if (control) debugLog("操作", `点击 ${describeControl(control)}`, "信息");
  };
  const onChange = (event: Event) => {
    const control = controlTarget(event.target);
    if (!control) return;
    const label = controlLabel(control);
    const value = controlValue(control, label);
    debugLog("操作", `更改 ${describeControl(control)}${value ? `：${value}` : ""}`, "信息");
  };
  const onInput = (event: Event) => {
    const control = controlTarget(event.target);
    if (!control || control.tagName !== "INPUT" || !["range", "color"].includes((control as HTMLInputElement).type)) return;
    const label = controlLabel(control);
    debugLog("操作", `调整 ${describeControl(control)}：${controlValue(control, label)}`, "信息");
  };
  const onSubmit = (event: Event) => {
    const form = event.target as Element | null;
    if (!form || form.tagName !== "FORM") return;
    const label = form.getAttribute("aria-label") || form.getAttribute("aria-labelledby") || "未命名表单";
    debugLog("操作", `提交表单「${oneLine(label)}」`, "信息");
  };
  target.addEventListener("error", onError, true);
  target.addEventListener("unhandledrejection", onRejection);
  target.addEventListener("click", onClick, true);
  target.addEventListener("change", onChange, true);
  target.addEventListener("input", onInput, true);
  target.addEventListener("submit", onSubmit, true);
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
    target.removeEventListener("click", onClick, true);
    target.removeEventListener("change", onChange, true);
    target.removeEventListener("input", onInput, true);
    target.removeEventListener("submit", onSubmit, true);
    Object.assign(scope.console, originals);
    watched.delete(target);
  };
}

/** 应用启动时开始收集主窗口信息；日志窗口尚未打开时只进入内存缓冲，不调用原生层。 */
export function startDiagnostics() {
  if (!isTauri() || started) return;
  started = true;
  stopMain = watchWindow(window, "页面");
  const probe = document.createElement("video");
  debugLog("环境", `${navigator.userAgent}\n  解码能力 ${CODECS.map(([name, type]) => `${name}=${probe.canPlayType(type) || "不支持"}`).join(" ")}`, "信息");
}

/** 由设置按钮调用：打开原生终端窗口，启用转发并补发打开窗口前缓冲的错误与操作。 */
export async function openDiagnostics(): Promise<void> {
  if (!isTauri()) throw new Error("仅桌面客户端支持打开调试日志窗口");
  startDiagnostics();
  await invoke("diagnostics_open");
  if (!enabled) {
    enabled = true;
    flushBuffer();
    reportRepeats();
    repeatTimer = setInterval(reportRepeats, 2000);
  }
  debugLog("调试日志", "日志窗口已打开", "信息");
}

/** 测试之间恢复初始状态：撤销主窗口监听、停止重复计数并清空缓冲。 */
export function resetDiagnostics() {
  stopMain?.();
  clearInterval(repeatTimer);
  started = enabled = false;
  stopMain = repeatTimer = undefined;
  buffer = [];
  bufferChars = 0;
  last = { key: "", level: "错误", source: "", repeats: 0 };
}
