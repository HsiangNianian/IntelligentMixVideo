/** 共享 API 地址：客户端自定义值优先，未指定时使用内置服务或 Vite 默认值。 */
let base = (import.meta.env.VITE_API_URL?.trim() || "http://localhost:20070").replace(/\/+$/, "");

/** 启动恢复或设置保存时更新，后续业务请求和新 SSE 连接读取同一地址。 */
export function setApiBase(url: string) {
  base = url.replace(/\/+$/, "");
}

/** 每次请求读取，避免模块加载早于内置服务启动时缓存旧地址。 */
export function apiBase() {
  return base;
}
