/** Bun 测试环境：注册 DOM、固定环境地址、拦截网络（含 EventSource 替身）、为每例换用独立的字效会话内存存储，并在每例后清理组件和 mock。 */
import { afterEach, beforeEach, mock, spyOn } from "bun:test";
import { GlobalRegistrator } from "@happy-dom/global-registrator";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";

// 禁止测试 iframe 导航访问真实服务，仍保留 contentWindow 供消息协议用例使用。
// 外部脚本与样式表（如预览 SDK）也不下载，直接按加载完成派发 load：它们走 happy-dom 自己的请求，不经过下面的 fetch 拦截。
GlobalRegistrator.register({ url: "http://localhost:1420", settings: {
  navigation: {disableChildFrameNavigation: true},
  disableJavaScriptFileLoading: true,
  disableCSSFileLoading: true,
  handleDisabledFileLoadingAsSuccess: true,
} });
// Web Animations 规范在 cancel() 拒绝 finished 时把该 promise 标记为已处理，浏览器不报错；happy-dom 没有标记，
// motion 动画中途卸载（例如移除正在出现的图片预览）会被 bun 报成未处理错误并算到当前用例上。
const cancelAnimation = Animation.prototype.cancel;
Animation.prototype.cancel = function cancel(this: Animation) {
  this.finished.catch(() => {});
  cancelAnimation.call(this);
};
process.env.VITE_API_URL = "http://api.test:8000/";
// 三个视频都用 public 资源路径；内置示例要读取打包的文件，只在专门的用例里模拟这些请求。
process.env.VITE_PREVIEW_VIDEO_1_URL = "/sample.mp4";
process.env.VITE_PREVIEW_VIDEO_2_URL = "/sample-2.mp4";
process.env.VITE_PREVIEW_VIDEO_3_URL = "/sample-3.mp4";
// 构建时由 Vite 从 package.json 注入；测试固定一个值，不随发版变化。
process.env.IMV_VERSION = "1.2.3";
process.env.IMV_COMMIT = "abc1234";

// React DOM 必须在浏览器全局对象注册之后加载，保证事件与 screen 绑定正确。
const { cleanup } = await import("@testing-library/react");
// 字效会话存储同样在浏览器全局对象之后加载；每例使用独立内存实现，不调用 Tauri。
const { resetUpdates } = await import("@/features/updates/store");
const { memoryStorage, setStorage } = await import("@/features/remotion_templates/localSessions");

/**
 * happy-dom 没有 EventSource：用记录连接的替身代替，不连接网络、默认不推送事件；用例经 `instances` 取得连接并用 `emit` 推送。
 */
export class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  /** 本例创建过的全部连接，按创建顺序。 */
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.OPEN;
  /** 登记连接地址。 */
  constructor(public url: string) {
    super();
    FakeEventSource.instances.push(this);
  }
  /** 推送一条具名事件；已关闭的连接不再收到事件，与浏览器一致。 */
  emit(type: string, data = "{}") {
    if (this.readyState !== FakeEventSource.CLOSED) this.dispatchEvent(new MessageEvent(type, { data }));
  }
  /** 关闭连接。 */
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}
Reflect.set(globalThis, "EventSource", FakeEventSource);

/** 默认拒绝未声明的请求，各测试只配置自己需要的响应，不连接实际服务。 */
export let fetchMock: ReturnType<typeof spyOn<typeof globalThis, "fetch">>;

/** 模拟桌面标记和 IPC，不注入全局 API；用例结束自动清理，也可用返回函数提前恢复浏览器环境。 */
export function mockDesktop(invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>) {
  Reflect.set(globalThis, "isTauri", true);
  mockIPC((command, args) => invoke(command, args as Record<string, unknown>));
  return () => {
    clearMocks();
    Reflect.deleteProperty(globalThis, "isTauri");
  };
}

beforeEach(() => {
  process.env.IMV_DEBUG = "false";
  localStorage.clear();
  FakeEventSource.instances = [];
  setStorage(memoryStorage());
  fetchMock = spyOn(globalThis, "fetch");
  fetchMock.mockRejectedValue(new Error("测试未配置此网络请求"));
});

afterEach(() => {
  cleanup();
  resetUpdates();
  clearMocks();
  Reflect.deleteProperty(globalThis, "isTauri");
  mock.restore();
});
