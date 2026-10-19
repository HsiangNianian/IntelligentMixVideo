/** 设计沙盒挂载入口：加载界面字体与沙盒样式，再挂载原型应用；与正式客户端的 main.tsx 互不引用。 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist/index.css";
import "@fontsource-variable/geist-mono/index.css";
import "./design.css";
import DesignApp from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DesignApp />
  </StrictMode>,
);
