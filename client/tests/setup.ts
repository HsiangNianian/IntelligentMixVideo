/** Bun 测试环境：注册 DOM、固定环境地址、拦截网络，并在每例后清理组件和 mock。 */
import { afterEach, beforeEach, mock, spyOn } from "bun:test";
import { GlobalRegistrator } from "@happy-dom/global-registrator";
import { clearMocks, mockIPC } from "@tauri-apps/api/mocks";

GlobalRegistrator.register({ url: "http://localhost:1420" });
process.env.VITE_API_URL = "http://api.test:8000/";
process.env.VITE_PREVIEW_VIDEO_URL = "/sample.mp4";

// React DOM 必须在浏览器全局对象注册之后加载，保证事件与 screen 绑定正确。
const { cleanup } = await import("@testing-library/react");

/** 默认拒绝未声明的请求，各测试只配置自己需要的响应，不连接实际服务。 */
export let fetchMock: ReturnType<typeof spyOn<typeof globalThis, "fetch">>;

/** 模拟桌面标记和 IPC，不注入全局 API；返回清理函数恢复普通浏览器环境。 */
export function mockDesktop(invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>) {
  Reflect.set(globalThis, "isTauri", true);
  mockIPC((command, args) => invoke(command, args as Record<string, unknown>));
  return () => {
    clearMocks();
    Reflect.deleteProperty(globalThis, "isTauri");
  };
}

beforeEach(() => {
  fetchMock = spyOn(globalThis, "fetch");
  fetchMock.mockRejectedValue(new Error("测试未配置此网络请求"));
});

afterEach(() => {
  cleanup();
  mock.restore();
});
