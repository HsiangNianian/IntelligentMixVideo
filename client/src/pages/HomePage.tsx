/** 首页布局将当前时间组件居中；页面不持有计时器或基础 UI 样式逻辑。 */
import CurrentTime from "@/components/CurrentTime";

/** 用响应式留白承载首页当前唯一的业务组件。 */
export default function HomePage() {
  return (
    <main className="grid min-h-svh place-items-center p-6">
      <CurrentTime />
    </main>
  );
}
