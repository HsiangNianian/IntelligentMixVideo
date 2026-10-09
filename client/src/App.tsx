/** 应用入口优先恢复客户端地址；未指定时等待内置服务就绪，再挂载首页与全局轻提示容器。 */
import HomePage from "@/pages/HomePage";
import { toast } from "sonner";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { setApiBase } from "@/lib/api-base";
import { readSettings } from "@/features/settings/api";
import { LoaderGooeyBlobs } from "@/components/ui/loaders-gooey-blobs";
import { MotionConfig } from "motion/react";
import { Toaster } from "@/components/ui/sonner";

/** 启动失败展示实际诊断，避免业务页面向尚未启动的后端发送请求。 */
export default function App() {
  const [ready, setReady] = useState(!isTauri());
  const [error, setError] = useState("");
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    // 设置损坏不能封锁离线模板；此时使用默认地址，避免用未知配置启动内置后端。
    readSettings().catch((reason) => {
      if (active) toast.warning(`无法读取客户端设置：${String(reason)}。使用默认后端地址，本地模板仍可使用；请修复设置。`, { id: "client-settings", duration: Infinity, closeButton: true });
      return null;
    }).then((saved) => {
      if (!active || saved === null) return null;
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
  // 全局轻提示容器与页面并列挂载，业务通过 sonner 的 toast() 发送保存结果等短提示。
  // MotionConfig 让所有 motion 动画遵循系统「减少动态效果」设置。
  if (ready) return <MotionConfig reducedMotion="user"><HomePage /><Toaster position="bottom-right" /></MotionConfig>;
  return <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8 text-center">
    {!error && <LoaderGooeyBlobs size={14} className="text-foreground/70" />}
    <p role={error ? "alert" : "status"} className={error ? "max-w-xl whitespace-pre-wrap text-sm text-destructive" : "max-w-xl whitespace-pre-wrap text-sm text-muted-foreground"}>
      {error || "正在启动内置服务，首次运行需要初始化数据库，请稍候…"}
    </p>
  </main>;
}
