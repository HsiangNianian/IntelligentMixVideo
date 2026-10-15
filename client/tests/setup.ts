/** Bun 测试环境：注册 DOM、固定环境地址、拦截网络、为每例换用独立的字效会话内存存储，并在每例后清理组件和 mock。 */
import { afterEach, beforeEach, mock, spyOn } from "bun:test";
import { GlobalRegistrator } from "@happy-dom/global-registrator";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";

// 禁止测试 iframe 导航访问真实服务，仍保留 contentWindow 供消息协议用例使用。
GlobalRegistrator.register({ url: "http://localhost:1420", settings: {navigation: {disableChildFrameNavigation: true}} });
// Web Animations 规范在 cancel() 拒绝 finished 时把该 promise 标记为已处理，浏览器不报错；happy-dom 没有标记，
// motion 动画中途卸载（例如移除正在出现的图片预览）会被 bun 报成未处理错误并算到当前用例上。
const cancelAnimation = Animation.prototype.cancel;
Animation.prototype.cancel = function cancel(this: Animation) {
  this.finished.catch(() => {});
  cancelAnimation.call(this);
};
process.env.VITE_API_URL = "http://api.test:8000/";
process.env.VITE_PREVIEW_VIDEO_1_URL = "/sample.mp4";
// 构建时由 Vite 从 package.json 注入；测试固定一个值，不随发版变化。
process.env.IMV_VERSION = "1.2.3";

// React DOM 必须在浏览器全局对象注册之后加载，保证事件与 screen 绑定正确。
const { cleanup } = await import("@testing-library/react");
// 字效会话存储同样在浏览器全局对象之后加载；每例使用独立内存实现，不调用 Tauri。
const { memoryStorage, setStorage } = await import("@/features/remotion_templates/localSessions");

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
  setStorage(memoryStorage());
  fetchMock = spyOn(globalThis, "fetch");
  fetchMock.mockRejectedValue(new Error("测试未配置此网络请求"));
});

afterEach(() => {
  cleanup();
  clearMocks();
  Reflect.deleteProperty(globalThis, "isTauri");
  mock.restore();
});
