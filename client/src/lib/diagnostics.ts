/**
 * 调试日志：桌面客户端持续在主窗口与模板预览 iframe 中记录错误、资源失败和控件操作；
 * 日志先保存在有界内存中，用户在设置里打开日志窗口后转发到原生终端日志文件；每条记录带发生时刻，
 * 补发的旧记录与原生日志按实际时间对照。业务动作、桌面命令与任务状态显式关联；设备与显卡探测、主线程长帧（卡顿）、HTTP 请求全生命周期、内存趋势、
 * 窗口与显示器变化只在日志窗口打开后采集，普通使用不受影响。
 * 浏览器不开启收集；相同记录合并，密码、密钥等敏感输入只记录“已隐藏”。
 */
import { invoke, isTauri, type InvokeArgs, type InvokeOptions } from "@tauri-apps/api/core";
import { apiBase, apiServiceKey } from "./api-base";

/** 日志级别与原生窗口的显示格式一致。 */
export type DiagnosticLevel = "错误" | "警告" | "信息";

/** 一条待转发记录；消息在进入 IPC 前统一截断，at 为发生时刻（Unix 毫秒）。 */
type Entry = { level: DiagnosticLevel; source: string; message: string; at: number };

/** 单条记录、内存缓冲与控件标签的上限，避免日志窗口和 IPC 被长文本刷满。 */
const MAX_LENGTH = 8000;
const MAX_BUFFER_ENTRIES = 400;
const MAX_BUFFER_CHARS = 200_000;
const MAX_CONTROL_LABEL = 100;

/** 媒体错误码（MediaError.code）的中文含义；预览界面提示也使用它。 */
export const MEDIA_ERRORS: Record<number, string> = { 1: "加载被中止", 2: "网络错误", 3: "解码失败", 4: "格式或地址不受支持" };
/**
 * 启动时报告的解码能力：桌面 WebView 是否能播放常见编码，定位“视频无法播放”时先看这一行。
 * 编码档次按 1080p 选取（H.264 High@4.0 等），同一组字符串也用于查询 1080p 是否走硬件解码。
 */
const CODECS: [string, string][] = [
  ["H.264", 'video/mp4; codecs="avc1.640028"'],
  ["H.265", 'video/mp4; codecs="hvc1.1.6.L120.90"'],
  ["VP9", 'video/webm; codecs="vp09.00.40.08"'],
  ["AV1", 'video/mp4; codecs="av01.0.08M.08"'],
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
/** 只继承当前同步调用的因果关系；异步后续必须显式携带，不能归到“最近一次点击”。 */
let context: string | undefined;
let interaction: { event: Event; id: string } | undefined;
let session = 0;

/** 同步进入一条执行链；调用方可在 await 之后再次使用保存的编号，不改写 Promise 或全局异步上下文。 */
export function inDiagnosticContext<T>(id: string | undefined, run: () => T): T {
  const previous = context;
  context = id;
  try { return run(); } finally { context = previous; }
}

/** 在入口保存当前操作编号，供异步门禁放行后沿用。 */
export function diagnosticContext() {
  return context ?? (interaction && interaction.event.eventPhase !== 0 ? interaction.id : undefined);
}

/** 仅包含宿主生成的编号与安全消息；不保存请求、配置或返回对象。 */
export type DiagnosticTrace = {
  id: string | undefined;
  run: <T>(work: () => T) => T;
  step: (message: string, level?: DiagnosticLevel) => void;
  finish: (message: string, level?: DiagnosticLevel) => void;
};

/** 记录执行开始、阶段和唯一终态；重置后迟到回执不进入新会话。 */
export function beginDiagnostic(source: string, label: string): DiagnosticTrace {
  const id = started ? crypto.randomUUID().replace(/-/g, "").slice(0, 16) : undefined;
  const generation = session;
  const parent = diagnosticContext();
  const begin = performance.now();
  let finished = false;
  const step = (message: string, level: DiagnosticLevel = "信息") => {
    if (id && generation === session && !finished) debugLog(source, `[#${id}] ${oneLine(message, MAX_LENGTH)}`, level);
  };
  step(`开始 ${label}${parent ? `；来自 #${parent}` : "；独立或后台触发"}`);
  return {
    id, step,
    run: (work) => inDiagnosticContext(id, work),
    finish(message, level = "信息") {
      step(`${label}：${message}（${duration(performance.now() - begin)}）`, level);
      finished = true;
    },
  };
}

/** API/业务边界的统一终态：覆盖响应解码、前置读取和本地落库失败，调用方保留原错误处理。 */
export async function diagnosticTask<T>(source: string, label: string, work: (trace: DiagnosticTrace) => Promise<T>): Promise<T> {
  const trace = beginDiagnostic(source, label);
  try {
    const result = await trace.run(() => work(trace));
    trace.finish("处理完成");
    return result;
  } catch (error) {
    const cancelled = error instanceof Error && error.name === "AbortError";
    trace.finish(`${cancelled ? "已取消" : "处理失败"}：${briefly(error)}`, cancelled ? "信息" : "错误");
    throw error;
  }
}

/** 活动任务保留关联；仅保留最近 200 个终态，不存任务输入、图片、配置或模型内容。 */
const tasks = new Map<string, { trace: DiagnosticTrace; state: string; parent?: string; terminal: boolean }>();
const completedTasks = new Set<string>();
const TASK_STATUSES = new Set(["queued", "running", "processing", "succeeded", "answered", "needs_input", "failed", "cancelled", "interrupted"]);
const TERMINAL_TASK_STATUSES = new Set(["succeeded", "answered", "needs_input", "failed", "cancelled", "interrupted"]);

/** 只接受有界机器标识，拒绝自由文本、换行和控制字符，不改变业务返回值。 */
function taskField(value: unknown, limit: number): string {
  return typeof value === "string" && value.length <= limit && /^[a-zA-Z0-9_-]+$/.test(value) ? value : "";
}

/** 服务必须是发起请求/订阅时保存的地址；只接收机器字段，禁止传入回答或错误详情。 */
export function diagnosticTaskState(kind: "字效" | "合成", id: string, status: string, details: { service: string; stage?: string; code?: string }) {
  if (!started || !taskField(id, 128)) return;
  const stage = taskField(details.stage, 64);
  const code = taskField(details.code, 64);
  status = TASK_STATUSES.has(status) ? status : "unknown";
  const parent = diagnosticContext();
  const service = apiServiceKey(details.service);
  const key = JSON.stringify([service, kind, id]);
  let task = tasks.get(key);
  if (!task) {
    task = { trace: beginDiagnostic("后端任务", `${kind}任务 ${id}（${oneLine(diagnosticText(service), 200)}）`), state: "", parent, terminal: false };
    tasks.set(key, task);
  } else if (parent && parent !== task.parent) {
    debugLog("后端任务", `[#${task.trace.id}] 任务 ${id} 观察操作 #${parent}`, "信息");
  }
  const state = `${status} ${stage} ${code}`;
  if (state === task.state) return;
  task.state = state;
  const detail = `状态 ${status}${stage ? `；阶段 ${stage}` : ""}${code ? `；错误码 ${code}` : ""}`;
  const level = status === "failed" || status === "interrupted" ? "错误" : "信息";
  if (task.terminal) debugLog("后端任务", `[#${task.trace.id}] 终态补充：${detail}`, level);
  else if (TERMINAL_TASK_STATUSES.has(status)) {
    task.trace.finish(detail, level);
    task.terminal = true;
    completedTasks.add(key);
    if (completedTasks.size > 200) {
      const oldest = completedTasks.values().next().value!;
      completedTasks.delete(oldest);
      tasks.delete(oldest);
    }
  } else task.trace.step(detail);
}

/** 对诊断文本中的 URL、常见凭据键和 Bearer 令牌脱敏；业务请求和返回值不整体序列化。 */
export function diagnosticText(value: string): string {
  return value
    .replace(/https?:\/\/[^\s"'<>「」；，（）]+/gi, (raw) => {
      try {
        const url = new URL(raw);
        return raw.split(/[?#]/, 1)[0].replace(/^(https?:\/\/)[^/]*@/i, "$1")
          .replace(url.pathname, redactMediaPath(url.hostname, url.pathname));
      }
      catch { return "[地址已隐藏]"; }
    })
    .replace(/\bBearer\s+[^\s"',;]+/gi, "Bearer [已隐藏]")
    .replace(/((?:password|passwd|secret|token|api[_-]?key|access[_-]?key|credential|authorization|密码|密钥)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)/gi, "$1[已隐藏]");
}

/** 所有业务 IPC 共用的开始/完成/失败记录；只记录命令与操作名，不记录参数值和返回数据。 */
export async function diagnosticInvoke<T>(command: string, args?: InvokeArgs, options?: InvokeOptions): Promise<T> {
  if (!started || command.startsWith("diagnostics_")) return invoke<T>(command, args, options);
  const fields = args && !Array.isArray(args) && !(args instanceof ArrayBuffer) && !(args instanceof Uint8Array) ? args as Record<string, unknown> : {};
  const operation = typeof fields.operation === "string" && /^[a-z_]{1,60}$/.test(fields.operation) ? `/${fields.operation}`
    : command === "local_settings" ? (fields.id ? "/保存设置" : "/读取设置") : "";
  const trace = beginDiagnostic("桌面命令", `${command}${operation}`);
  try {
    const result = await invoke<T>(command, command === "client_update" ? { ...fields, diagnosticId: trace.id } : args, options);
    trace.finish("执行完成");
    return result;
  } catch (error) {
    trace.finish(`执行失败：${diagnosticText(briefly(error))}`, "错误");
    throw error;
  }
}

/** 仅在事件仍处于派发阶段时提供编号；原生事件监听器之间可以运行微任务，不能靠微任务提前清除。 */
function recordInteraction(event: Event, message: string) {
  if (!started) return;
  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  interaction = { event, id };
  debugLog(`前端操作 #${id}`, message, "信息");
}

/** 交给原生层输出，带上发生时刻；失败时静默，不能再写 console，否则会被自己的监听再次转发。 */
function send(level: DiagnosticLevel, source: string, message: string, at = Date.now()) {
  void invoke("diagnostics_log", { level, source, message: message.slice(0, MAX_LENGTH), at }).catch(() => {});
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
  for (const entry of entries) send(entry.level, entry.source, entry.message, entry.at);
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
  const clipped = diagnosticText(message).slice(0, MAX_LENGTH);
  const key = `${level}\n${source}\n${clipped}`;
  if (key === last.key) {
    last.repeats++;
    return;
  }
  reportRepeats();
  last = { key, level, source, repeats: 0 };
  if (enabled) send(level, source, clipped);
  else remember({ level, source, message: clipped, at: Date.now() });
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

/** 错误的名称与原因，不带调用栈：网络与连接错误的调用栈只指向 fetch 或读取流本身，没有排查价值。 */
export function briefly(value: unknown): string {
  return value instanceof Error ? `${value.name}: ${value.message}` : describe(value);
}

/** 预览媒体服务的启动令牌是路径中的 32 位十六进制段；日志只保留占位符，避免把访问凭据带进可分享的日志。 */
const MEDIA_TOKEN_PATH = /^\/[0-9a-f]{32}(?=\/)/i;

/** 本机预览媒体服务地址中的令牌脱敏；其他地址保持原样。 */
function redactMediaPath(hostname: string, pathname: string): string {
  return hostname === "127.0.0.1" ? pathname.replace(MEDIA_TOKEN_PATH, "/media-token") : pathname;
}

/** 媒体地址脱敏；解析失败时保留原始文本。 */
function redactMediaUrl(value: string): string {
  try {
    const url = new URL(value, window.location.href);
    const pathname = redactMediaPath(url.hostname, url.pathname);
    if (pathname === url.pathname) return value;
    url.pathname = pathname;
    return url.href;
  } catch {
    return value;
  }
}

/** 媒体元素的地址、错误码与加载状态；报错时仍保留在元素上，须在清除地址之前读取。 */
export function describeMedia(media: HTMLMediaElement): string {
  const error = media.error;
  const reason = error ? `${error.code}（${MEDIA_ERRORS[error.code] ?? "未知"}）${error.message ? ` ${error.message}` : ""}` : "无";
  return `地址 ${redactMediaUrl(media.currentSrc || media.src || "（无）")}；媒体错误 ${reason}；networkState=${media.networkState} readyState=${media.readyState}`;
}

/** 将空白和多行压成一行，避免控件名称破坏日志的单条记录格式。 */
export function oneLine(value: string, limit = MAX_CONTROL_LABEL): string {
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
    if (control) recordInteraction(event, `点击 ${describeControl(control)}`);
  };
  const onChange = (event: Event) => {
    const control = controlTarget(event.target);
    if (!control) return;
    const label = controlLabel(control);
    const value = controlValue(control, label);
    recordInteraction(event, `更改 ${describeControl(control)}${value ? `：${value}` : ""}`);
  };
  const onInput = (event: Event) => {
    const control = controlTarget(event.target);
    if (!control || control.tagName !== "INPUT" || !["range", "color"].includes((control as HTMLInputElement).type)) return;
    const label = controlLabel(control);
    recordInteraction(event, `调整 ${describeControl(control)}：${controlValue(control, label)}`);
  };
  const onSubmit = (event: Event) => {
    const form = event.target as Element | null;
    if (!form || form.tagName !== "FORM") return;
    const label = form.getAttribute("aria-label") || form.getAttribute("aria-labelledby") || "未命名表单";
    recordInteraction(event, `提交表单「${oneLine(label)}」`);
  };
  const onInvalid = (event: Event) => {
    const control = controlTarget(event.target) as HTMLInputElement | null;
    const parent = diagnosticContext();
    if (control) debugLog("前端校验", `${parent ? `[#${parent}] ` : ""}${describeControl(control)} 未通过表单校验：${control.validationMessage}；未提交`, "警告");
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.repeat) return;
    const control = controlTarget(event.target);
    const shortcut = (event.ctrlKey || event.metaKey) && ["s", "z", "y", "enter"].includes(event.key.toLowerCase());
    if (shortcut || ["Enter", "Escape"].includes(event.key) || (event.key === " " && control?.matches("button,[role='button']"))) {
      recordInteraction(event, `按键 ${event.metaKey ? "⌘+" : ""}${event.ctrlKey ? "Ctrl+" : ""}${event.shiftKey ? "Shift+" : ""}${event.key === " " ? "Space" : event.key}${control ? `；${describeControl(control)}` : ""}`);
    }
  };
  target.addEventListener("error", onError, true);
  target.addEventListener("unhandledrejection", onRejection);
  target.addEventListener("click", onClick, true);
  target.addEventListener("change", onChange, true);
  target.addEventListener("input", onInput, true);
  target.addEventListener("submit", onSubmit, true);
  target.addEventListener("invalid", onInvalid, true);
  target.addEventListener("keydown", onKey, true);
  const originals = { error: scope.console.error, warn: scope.console.warn };
  for (const [method, level] of [["error", "错误"], ["warn", "警告"]] as const) {
    // 没有内容的输出（SDK 初始化时会打一条空警告）不转发，避免日志里出现空记录。
    scope.console[method] = (...args: unknown[]) => {
      originals[method].apply(scope.console, args);
      const message = args.map(describe).join(" ");
      if (message.trim()) debugLog(source, message, level);
    };
  }
  return () => {
    target.removeEventListener("error", onError, true);
    target.removeEventListener("unhandledrejection", onRejection);
    target.removeEventListener("click", onClick, true);
    target.removeEventListener("change", onChange, true);
    target.removeEventListener("input", onInput, true);
    target.removeEventListener("submit", onSubmit, true);
    target.removeEventListener("invalid", onInvalid, true);
    target.removeEventListener("keydown", onKey, true);
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

/** 日志窗口是否已打开：卡顿统计等需要额外开销的采集只在打开后进行，普通使用不受影响。 */
export function diagnosticsEnabled(): boolean {
  return enabled;
}

/**
 * 报告设备与显卡：CPU 线程数与内存（Chromium 才有）、WebGL 实际使用的显卡与图形后端（Windows 上是 ANGLE 的 Direct3D），
 * 以及 1080p 各编码能否解码、是否走硬件（MediaCapabilities 的 powerEfficient）。黑屏、卡顿多与显卡驱动、软件解码或
 * 低配设备有关。打开日志窗口时才探测；WebGL 上下文读取后立即释放，不支持的接口记为不可用，不影响其他记录。
 */
function reportGraphics() {
  const gl = document.createElement("canvas").getContext("webgl");
  const debug = gl?.getExtension("WEBGL_debug_renderer_info");
  const renderer = !gl ? "WebGL 不可用"
    : debug ? `${gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)} / ${gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)}`
    : String(gl.getParameter(gl.RENDERER));
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  debugLog("环境", `显卡 ${renderer}；devicePixelRatio=${window.devicePixelRatio}；CPU ${navigator.hardwareConcurrency || "未知"} 线程${memory ? `，内存约 ${memory} GB` : ""}`, "信息");
  if (!navigator.mediaCapabilities) return;
  void Promise.all(CODECS.map(async ([name, type]) => {
    try {
      const result = await navigator.mediaCapabilities.decodingInfo({ type: "file", video: { contentType: type, width: 1920, height: 1080, bitrate: 8_000_000, framerate: 30 } });
      if (!result.supported) return `${name}=不支持`;
      return `${name}=${result.powerEfficient ? "硬件" : "软件"}${result.smooth ? "" : "（可能卡顿）"}`;
    } catch {
      return `${name}=未知`;
    }
  })).then((items) => debugLog("环境", `1080p 解码 ${items.join(" ")}`, "信息"));
}

/** 原生层打开日志的结果：日志文件路径，以及终端窗口没能打开时的原因（文件照常写入）。 */
export type DiagnosticsOpened = { path: string; viewerError: string | null };

/**
 * 由设置按钮调用：打开日志文件与原生终端窗口，启用转发并补发打开前缓冲的错误与操作。
 * 终端打不开（例如 Linux 没有已知的终端程序）时仍启用转发，由调用方提示日志文件位置。
 */
export async function openDiagnostics(): Promise<DiagnosticsOpened> {
  if (!isTauri()) throw new Error("仅桌面客户端支持打开调试日志窗口");
  startDiagnostics();
  const opened = await invoke<DiagnosticsOpened>("diagnostics_open");
  if (!enabled) {
    enabled = true;
    flushBuffer();
    reportRepeats();
    repeatTimer = setInterval(reportRepeats, 2000);
    reportGraphics();
    observeLongFrames();
    stopPageWatchers = [watchRequests(), watchMemory(), watchDisplay()];
  }
  return opened;
}

/** 打开日志窗口后启动的页面采集（请求、内存、窗口），重置时逐个停止。 */
let stopPageWatchers: (() => void)[] = [];

/** Chromium 才有的 JS 堆用量（字节）；其他 WebView 返回 undefined。 */
export function heapUsed(): number | undefined {
  return (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize;
}

/** 字节数显示为一位小数的 MB。 */
function megabytes(bytes: number): string {
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

/** 成功但超过它的请求也记录用时。 */
const SLOW_REQUEST_MS = 3000;

/**
 * 请求的简短描述：方法与地址，去掉查询参数与片段（可能含签名或令牌），同源只写路径。
 * Tauri 的 IPC 与资源协议（ipc、asset、tauri.localhost）不是业务请求，返回 undefined 不记录，日志转发本身也走 IPC。
 */
function requestLabel(input: RequestInfo | URL, init?: RequestInit): string | undefined {
  let url: URL;
  try {
    url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
  } catch {
    return undefined;
  }
  if (!/^https?:$/.test(url.protocol) || /^(ipc|asset|tauri)\.localhost$/.test(url.hostname)) return undefined;
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  return `${method} ${url.origin === window.location.origin ? "" : url.host}${redactMediaPath(url.hostname, url.pathname)}`;
}

/** 失败响应正文最多读取的字节数：足够覆盖 200 个中文字符，超过后取消读取，避免大响应或流式响应拖住日志。 */
const RESPONSE_DETAIL_BYTES = 1024;

/** 失败响应的说明摘要：只读取文本或 JSON 正文的开头，二进制（如 Protobuf）不读。读取副本，不影响调用方。 */
async function responseDetail(response: Response): Promise<string> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    if (!/json|text/.test(response.headers.get("content-type") ?? "")) return "";
    reader = response.clone().body?.getReader();
    if (!reader) return "";
    const deadline = new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => {
      timeout = setTimeout(() => resolve({ done: true, value: undefined }), 500);
    });
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (size < RESPONSE_DETAIL_BYTES) {
      const { done, value } = await Promise.race([reader.read(), deadline]);
      if (done) break;
      if (!value?.length) continue;
      chunks.push(value);
      size += value.length;
    }
    const bytes = new Uint8Array(Math.min(size, RESPONSE_DETAIL_BYTES));
    let offset = 0;
    for (const chunk of chunks) {
      const part = chunk.subarray(0, bytes.length - offset);
      bytes.set(part, offset);
      offset += part.length;
      if (offset >= bytes.length) break;
    }
    let detail = new TextDecoder().decode(bytes);
    if (/json/.test(response.headers.get("content-type") ?? "")) {
      // FastAPI 的 input/ctx 可能回显密钥；只取公开说明，禁止整体序列化错误对象。
      const body = JSON.parse(detail) as { detail?: unknown; message?: unknown };
      const reason = body.detail ?? body.message;
      detail = typeof reason === "string" ? reason : Array.isArray(reason)
        ? reason.map(item => typeof item?.msg === "string" ? item.msg : "").filter(Boolean).join("；") : "";
    }
    const text = oneLine(diagnosticText(detail), 200);
    return text ? `：${text}` : "";
  } catch {
    return "";
  } finally {
    clearTimeout(timeout);
    if (reader) void reader.cancel().catch(() => {});
  }
}

/**
 * 每条请求记录开始与响应/失败/取消，只向配置的业务 API 发送关联编号；不改第三方请求或消费业务响应。
 * SSE 收到响应头只表示连接建立，不能表示任务完成。HTTP 错误与网络未获响应明确区分。
 */
function watchRequests(): () => void {
  const unpatched = window.fetch;
  const original = unpatched.bind(window);
  // 旧服务端不允许新增 CORS 请求头；收到明确支持回执之后才发送编号，不做失败后重试。
  const supported = new Set<string>();
  const patched = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const label = requestLabel(input, init);
    if (!label) return original(input, init);
    const trace = beginDiagnostic("前端请求", label);
    const generation = session;
    const requestStart = performance.now();
    let requestInit = init;
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
    const base = new URL(apiBase(), window.location.href);
    const mode = init?.mode ?? (input instanceof Request ? input.mode : undefined);
    const business = url.origin === base.origin && (url.pathname === base.pathname || url.pathname.startsWith(base.pathname.replace(/\/$/, "") + "/"));
    if (business && supported.has(base.href) && mode !== "no-cors" && trace.id) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      headers.set("X-IMV-Request-ID", trace.id);
      requestInit = { ...init, headers };
    }
    try {
      const response = await original(input, requestInit);
      if (business && response.headers.get("X-IMV-Diagnostics") === "1") supported.add(base.href);
      const elapsed = performance.now() - requestStart;
      const serverMs = response.headers.get("X-IMV-Elapsed-Ms");
      const streaming = response.headers.get("content-type")?.includes("text/event-stream");
      const level = !response.ok ? response.status >= 500 ? "错误" : "警告" : elapsed > SLOW_REQUEST_MS ? "警告" : "信息";
      const source = business ? "后端响应" : "网络响应";
      if (generation === session) debugLog(source, `[#${trace.id}] ${label} → HTTP ${response.status}${serverMs && /^\d+(\.\d+)?$/.test(serverMs) ? `；后端处理 ${serverMs} ms` : ""}`, level);
      trace.finish(streaming && response.ok ? "事件流连接已建立，等待后续任务事件" : response.ok ? "已收到成功响应" : "已收到失败响应", level);
      if (!response.ok) void responseDetail(response).then(detail => {
        if (detail && generation === session) debugLog(source, `[#${trace.id}] 错误说明${detail}`, level);
      }).catch(() => {});
      return response;
    } catch (error) {
      const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
      const aborted = signal?.aborted || (error instanceof Error && error.name === "AbortError");
      trace.finish(aborted ? `请求已取消/超时：${briefly(signal?.reason ?? error)}` : `未获得 HTTP 响应（网络、跨域或连接故障）：${briefly(error)}`, aborted ? "信息" : "错误");
      throw error;
    }
  };
  window.fetch = patched as typeof window.fetch;
  // 只在仍是本次替换时恢复，避免覆盖之后别处（如测试替身）再次替换的 fetch。
  return () => { if (window.fetch === patched) window.fetch = unpatched; };
}

/** 内存趋势的采样间隔。 */
const MEMORY_INTERVAL_MS = 60_000;

/**
 * 每分钟记录一次 JS 堆（Chromium 才有）及较上次的变化、页面元素与 iframe 数量，用来发现长时间使用后的泄漏
 * （例如每次修改都重建的 SDK 视频、没有卸载的 AI 资产播放器）。返回停止函数。
 */
function watchMemory(): () => void {
  let previous = heapUsed();
  let previousMessage = "";
  const timer = setInterval(() => {
    const heap = heapUsed();
    const change = heap !== undefined && previous !== undefined ? `（较上次 ${heap >= previous ? "+" : "-"}${megabytes(Math.abs(heap - previous))}）` : "";
    const message = `${heap !== undefined ? `JS 堆 ${megabytes(heap)}${change}，` : ""}页面元素 ${document.getElementsByTagName("*").length} 个，iframe ${document.getElementsByTagName("iframe").length} 个`;
    if (message !== previousMessage) debugLog("内存", message, "信息");
    previousMessage = message;
    previous = heap;
  }, MEMORY_INTERVAL_MS);
  return () => clearInterval(timer);
}

/**
 * 记录窗口与显示器变化：缩放倍数（换显示器或改系统缩放，画面可能变糊或变慢）、页面转入后台（最小化时 SDK 自动暂停预览）、
 * 进出全屏，以及停止调整后一秒的窗口大小。返回撤销监听的函数。
 */
function watchDisplay(): () => void {
  let ratio: MediaQueryList | undefined;
  let resizeTimer: ReturnType<typeof setTimeout> | undefined;
  /** 监听当前缩放倍数失效；每次变化后按新的倍数重新监听。 */
  const watchRatio = () => {
    ratio?.removeEventListener("change", ratioChanged);
    ratio = typeof window.matchMedia === "function" ? window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`) : undefined;
    ratio?.addEventListener("change", ratioChanged);
  };
  const ratioChanged = () => {
    debugLog("窗口", `缩放倍数变为 ${window.devicePixelRatio}（换了显示器或修改了系统缩放）`, "信息");
    watchRatio();
  };
  const visibility = () => debugLog("窗口", document.hidden ? "页面转入后台（最小化或被遮挡），预览会自动暂停" : "页面回到前台", "信息");
  const fullscreen = () => debugLog("窗口", document.fullscreenElement ? "进入全屏" : "退出全屏", "信息");
  const resized = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => debugLog("窗口", `窗口大小 ${window.innerWidth}×${window.innerHeight}`, "信息"), 1000);
  };
  watchRatio();
  document.addEventListener("visibilitychange", visibility);
  document.addEventListener("fullscreenchange", fullscreen);
  window.addEventListener("resize", resized);
  return () => {
    ratio?.removeEventListener("change", ratioChanged);
    document.removeEventListener("visibilitychange", visibility);
    document.removeEventListener("fullscreenchange", fullscreen);
    window.removeEventListener("resize", resized);
    clearTimeout(resizeTimer);
  };
}

/** 主线程长帧：绝对开始时刻（毫秒）、时长、阻塞时长、样式布局与绘制时长，以及各脚本的来源、函数与时长。 */
export type LongFrame = { start: number; duration: number; blocking: number; render: number; scripts: { source: string; fn: string; duration: number }[] };
/** long-animation-frame 条目里用到的字段；TypeScript 的 DOM 类型尚未收录该接口。 */
type AnimationFrameEntry = PerformanceEntry & {
  blockingDuration?: number;
  renderStart?: number;
  scripts?: { sourceURL?: string; sourceFunctionName?: string; invoker?: string; duration: number }[];
};

/** 播放之外单独记录的长帧下限；更短的停顿用户通常察觉不到，播放中的长帧交给播放汇总。 */
const LONG_FRAME_LOG_MS = 300;
const longFrameListeners = new Set<(frame: LongFrame) => void>();
let stopLongFrames: (() => void) | undefined;

/** 脚本来源的简称：阿里预览 SDK、应用自身的代码，其他来源按主机名。 */
function scriptSource(url: string): string {
  if (url.includes("video-editing-websdk")) return "阿里 SDK";
  try {
    const parsed = new URL(url);
    return parsed.origin === window.location.origin ? "应用" : parsed.host;
  } catch {
    return "未知来源";
  }
}

/** 毫秒数的显示：一秒以内用毫秒，以上用一位小数的秒。 */
function duration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} 秒`;
}

/**
 * 汇总一组长帧：次数、总时长、阻塞与样式布局绘制时长，以及按脚本来源合计、耗时最多的三个来源（各带耗时最多的函数）。
 * 播放汇总与播放之外的单个长帧共用。
 */
export function summarizeLongFrames(frames: LongFrame[]): string {
  const total = frames.reduce((sum, frame) => sum + frame.duration, 0);
  const blocking = frames.reduce((sum, frame) => sum + frame.blocking, 0);
  const render = frames.reduce((sum, frame) => sum + frame.render, 0);
  const sources = new Map<string, { duration: number; functions: Map<string, number> }>();
  for (const script of frames.flatMap((frame) => frame.scripts)) {
    const source = sources.get(script.source) ?? { duration: 0, functions: new Map<string, number>() };
    source.duration += script.duration;
    source.functions.set(script.fn, (source.functions.get(script.fn) ?? 0) + script.duration);
    sources.set(script.source, source);
  }
  const top = [...sources].sort((a, b) => b[1].duration - a[1].duration).slice(0, 3).map(([name, source]) => {
    const [fn] = [...source.functions].sort((a, b) => b[1] - a[1])[0];
    return `${name} ${duration(source.duration)}（${fn}）`;
  });
  return `${frames.length} 次共 ${duration(total)}（阻塞 ${duration(blocking)}，样式布局与绘制 ${duration(render)}）${top.length ? `，主要来自 ${top.join("、")}` : ""}`;
}

/** 把性能条目整理成长帧；longtask 没有脚本归因与渲染阶段，阻塞时长按超过 50 ms 的部分估算。 */
function longFrame(entry: AnimationFrameEntry): LongFrame {
  const end = entry.startTime + entry.duration;
  return {
    start: performance.timeOrigin + entry.startTime,
    duration: entry.duration,
    blocking: entry.blockingDuration ?? Math.max(0, entry.duration - 50),
    render: entry.renderStart ? end - entry.renderStart : 0,
    // 没有函数名时用触发方（如 VIDEO[src="…"].oncanplaythrough），去掉方括号里的地址，避免日志过长和写出视频地址。
    scripts: (entry.scripts ?? []).map((script) => ({ source: scriptSource(script.sourceURL ?? ""), fn: script.sourceFunctionName || (script.invoker ?? "").replace(/\[[^\]]*\]/g, "") || "匿名函数", duration: script.duration })),
  };
}

/**
 * 日志窗口打开后观察主线程长帧（long-animation-frame，不支持时退回 longtask；WebKitGTK 两者都不支持）。
 * 同源 iframe（模板预览 SDK）的长帧也报到主窗口并带脚本归因。有播放汇总在收集时交给它，否则超过 300 ms 的逐条记录。
 */
function observeLongFrames() {
  const types = typeof PerformanceObserver === "undefined" ? [] : PerformanceObserver.supportedEntryTypes ?? [];
  const type = types.includes("long-animation-frame") ? "long-animation-frame" : types.includes("longtask") ? "longtask" : undefined;
  if (!type) return;
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as AnimationFrameEntry[]) {
      const frame = longFrame(entry);
      if (longFrameListeners.size) for (const listener of longFrameListeners) listener(frame);
      else if (frame.duration >= LONG_FRAME_LOG_MS) debugLog("页面", `主线程卡顿 ${summarizeLongFrames([frame])}`, "警告");
    }
  });
  try {
    observer.observe({ type });
  } catch {
    return;
  }
  stopLongFrames = () => observer.disconnect();
}

/** 是否在统计主线程长帧；不支持的 WebView 上播放汇总注明无法统计。 */
export function longFramesObserved(): boolean {
  return stopLongFrames !== undefined;
}

/** 收集期间接收每个长帧（播放汇总使用），返回取消函数；有接收者时长帧不再逐条记录。 */
export function onLongFrame(listener: (frame: LongFrame) => void): () => void {
  longFrameListeners.add(listener);
  return () => { longFrameListeners.delete(listener); };
}

/** 测试之间恢复初始状态：撤销主窗口监听、停止重复计数并清空缓冲。 */
export function resetDiagnostics() {
  session++;
  context = undefined;
  interaction = undefined;
  tasks.clear();
  completedTasks.clear();
  stopMain?.();
  stopLongFrames?.();
  longFrameListeners.clear();
  for (const stop of stopPageWatchers) stop();
  stopPageWatchers = [];
  clearInterval(repeatTimer);
  started = enabled = false;
  stopMain = repeatTimer = stopLongFrames = undefined;
  buffer = [];
  bufferChars = 0;
  last = { key: "", level: "错误", source: "", repeats: 0 };
}
