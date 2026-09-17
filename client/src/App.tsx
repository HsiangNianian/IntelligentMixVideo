/** 应用入口等待桌面内置服务就绪后挂载首页，浏览器及普通安装包保留原 API 地址。 */
import HomePage from "@/pages/HomePage";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { setApiBase } from "@/lib/api-base";

/** 启动失败展示实际诊断，避免业务页面向尚未启动的后端发送请求。 */
export default function App() {
  const [ready, setReady] = useState(!isTauri());
  const [error, setError] = useState("");
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    invoke<string | null>("start_backend").then((url) => {
      if (!active) return;
      if (url) setApiBase(url);
      setReady(true);
    }).catch((reason) => {
      if (active) setError(String(reason));
    });
    return () => { active = false; };
  }, []);
  if (ready) return <HomePage />;
  return <main className="flex min-h-screen items-center justify-center p-8">
    <p role={error ? "alert" : "status"} className="max-w-xl whitespace-pre-wrap">
      {error || "正在启动内置服务，首次运行需要初始化数据库，请稍候…"}
    </p>
  </main>;
}
