/** 应用入口优先恢复客户端地址；未指定时等待内置服务就绪，再挂载首页。 */
import HomePage from "@/pages/HomePage";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { setApiBase } from "@/lib/api-base";
import { readSettings } from "@/features/settings/api";

/** 启动失败展示实际诊断，避免业务页面向尚未启动的后端发送请求。 */
export default function App() {
  const [ready, setReady] = useState(!isTauri());
  const [error, setError] = useState("");
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    readSettings().then((saved) => {
      if (!active) return null;
      const url = saved.$client?.api_url;
      return typeof url === "string" && url ? url : invoke<string | null>("start_backend");
    }).then((url) => {
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
