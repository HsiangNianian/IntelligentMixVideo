/** 把窗口标题设为「应用名 - 版本号」：浏览器改页面标题，Tauri 桌面另改原生窗口标题栏（后者不跟随网页标题）。 */
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useEffect } from "react";
import { WINDOW_TITLE } from "./version";

/** 挂载时设置一次标题；原生标题栏设置失败不影响使用，网页标题仍已更新。 */
export function useWindowTitle() {
  useEffect(() => {
    document.title = WINDOW_TITLE;
    if (!isTauri()) return;
    try {
      void getCurrentWindow().setTitle(WINDOW_TITLE).catch(() => undefined);
    } catch {
      // 窗口信息不可用时保留网页标题，不让标题设置影响界面渲染。
    }
  }, []);
}
