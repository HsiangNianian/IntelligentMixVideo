/** API 地址边界：普通构建使用 Vite 配置，内置服务就绪后使用其实际回环端口。 */
let base = (import.meta.env.VITE_API_URL?.trim() || "http://localhost:20070").replace(/\/+$/, "");

/** 仅由桌面启动完成回执设置，业务请求和 SSE 共享同一地址。 */
export function setApiBase(url: string) {
  base = url.replace(/\/+$/, "");
}

/** 每次请求读取，避免模块加载早于内置服务启动时缓存旧地址。 */
export function apiBase() {
  return base;
}
