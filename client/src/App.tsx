/** 应用入口优先恢复客户端地址；未指定时等待内置服务就绪，再挂载首页与全局轻提示容器。 */
import { initializeUpdates } from "@/features/updates/store";
import HomePage from "@/pages/HomePage";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useCallback, useEffect, useState } from "react";
import { setApiBase } from "@/lib/api-base";
import { readSettings } from "@/features/settings/api";
import { Splash } from "@/components/studio/Splash";
import { LogoMark } from "@/components/studio/Logo";
import { MotionConfig } from "motion/react";
import { Toaster } from "@/components/ui/sonner";
import { useWindowTitle } from "@/lib/window-title";

/** 启动失败展示实际诊断，避免业务页面向尚未启动的后端发送请求。 */
export default function App() {
  useWindowTitle();
  const [ready, setReady] = useState(!isTauri());
  const [error, setError] = useState("");
  const [splash, setSplash] = useState(true);
  const finishSplash = useCallback(() => setSplash(false), []);
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    Promise.all([readSettings(), initializeUpdates()]).then(([saved]) => {
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
  return <MotionConfig reducedMotion="user">
    {ready ? <HomePage logoHidden={splash} /> : <main className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-canvas p-8 text-center">
      <LogoMark className="size-14" />
      <h1 className="text-lg font-semibold">{error ? "内置服务没有启动成功" : "正在启动内置服务"}</h1>
      <p role={error ? "alert" : "status"} className={error ? "max-w-xl whitespace-pre-wrap text-sm text-danger" : "max-w-xl text-sm text-fg-muted"}>{error || "首次运行需要初始化数据库，请稍候…"}</p>
      {!error && <span className="h-1 w-48 overflow-hidden rounded-full bg-raised"><span className="block h-full w-1/3 animate-sweep bg-brand" /></span>}
    </main>}
    {splash && <Splash holding={!ready && !error} handoff={ready} onDone={finishSplash} />}
    <Toaster position="bottom-right" />
  </MotionConfig>;
}
