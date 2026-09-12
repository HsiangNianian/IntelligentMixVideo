/** 应用根组件只组合页面，时间状态与展示由首页内的组件负责。 */
import HomePage from "@/pages/HomePage";

/** 挂载首页；未来确有多页需求时才增加路由。 */
export default function App() {
  return <HomePage />;
}
