/** 首页组合右上角时钟与模板工作区；计时、模板状态和预览由各自组件管理。 */
import CurrentTime from "@/components/CurrentTime";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";

/** 模板管理入口使用响应式布局，小屏幕将编辑与预览上下排列。 */
export default function HomePage() {
  return (
    <main className="mx-auto max-w-7xl space-y-7 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <p className="text-xs font-medium tracking-widest text-muted-foreground">
            INTELLIGENT MIX VIDEO
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">特效模板</h1>
          <p className="text-sm text-muted-foreground">
            组合文字与画面效果，保存为可重复使用的模板。
          </p>
        </div>
        <CurrentTime />
      </header>
      <TemplateWorkspace />
    </main>
  );
}
