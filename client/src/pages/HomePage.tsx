/** 首页以侧边导航组合工作区和设置；业务面板隐藏时保留草稿、播放器与订阅。 */
import { cn } from "@/lib/utils";
import { useState } from "react";
import { Film, LayoutTemplate, Settings, Sparkles } from "lucide-react";
import CurrentTime from "@/components/CurrentTime";
import { SettingsPanel } from "@/components/SettingsPanel";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { Tabs as TabsPrimitive } from "radix-ui";
import { TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** 三个固定入口共享侧栏样式；保留文字的无障碍名称，窄屏只显示图标。 */
const navigation = [
  { value: "library", label: "模板库", icon: LayoutTemplate },
  { value: "remotion", label: "Remotion 字效", icon: Sparkles },
  { value: "settings", label: "设置", icon: Settings },
];

/** 工作区首次打开后仅隐藏；根使用 Radix 避免竖向 group 样式影响嵌套横向标签。 */
export default function HomePage() {
  const [workspace, setWorkspace] = useState("remotion");
  const [libraryOpened, setLibraryOpened] = useState(false);
  return (
    <TabsPrimitive.Root
      orientation="vertical"
      value={workspace}
      onValueChange={(value) => {
        // 模板库首次访问才加载 SDK；之后切换不卸载未保存的编辑状态。
        if (value === "library") setLibraryOpened(true);
        setWorkspace(value);
      }}
      className="flex min-h-dvh gap-0"
    >
      <aside className="sticky top-0 flex h-dvh w-16 shrink-0 flex-col border-r bg-card px-2 py-6 md:w-52 md:px-4">
        <div className="mb-10 flex items-center justify-center gap-3 md:justify-start md:px-2">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Film className="size-5" aria-hidden="true" />
          </div>
          <div className="hidden md:block">
            <p className="text-sm font-semibold tracking-tight">IntelligentMixVideo</p>
            <p className="mt-0.5 text-xs text-muted-foreground">视频创作工作台</p>
          </div>
        </div>
        <p className="mb-3 hidden px-3 text-[11px] font-medium tracking-widest text-muted-foreground md:block">工作空间</p>
        <TabsList aria-label="模板工作区" className="grid w-full flex-1 grid-cols-1 grid-rows-[auto_auto_1fr] items-start justify-start gap-2 rounded-none bg-transparent p-0">
          {navigation.map(({ value, label, icon: Icon }) => (
            <TabsTrigger
              key={value}
              value={value}
              title={label}
              className={cn("h-11 w-full flex-none gap-3 rounded-lg px-3 py-3 justify-center md:justify-start hover:bg-muted data-[state=active]:bg-accent data-[state=active]:text-primary group-data-[variant=default]/tabs-list:data-[state=active]:shadow-none", value === "settings" && "self-end")}
            >
              <Icon className="size-[18px]" aria-hidden="true" />
              <span className="sr-only md:not-sr-only">{label}</span>
            </TabsTrigger>
          ))}
        </TabsList>
      </aside>
      <main className="min-w-0 flex-1 px-3 py-5 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1600px] space-y-6">
          <header className="flex flex-wrap items-center justify-between gap-4 border-b pb-5">
            <div className="space-y-1.5">
              <p className="text-xs font-medium tracking-widest text-muted-foreground">INTELLIGENT MIX VIDEO</p>
              <h1 className="text-2xl font-semibold tracking-tight">{workspace === "settings" ? "设置" : "特效模板"}</h1>
            </div>
            <CurrentTime />
          </header>
          <TabsContent value="library" forceMount hidden={workspace !== "library"}>
            {libraryOpened && <TemplateWorkspace />}
          </TabsContent>
          <TabsContent value="remotion" forceMount hidden={workspace !== "remotion"}>
            <RemotionWorkspace />
          </TabsContent>
          <TabsContent value="settings">
            <SettingsPanel />
          </TabsContent>
        </div>
      </main>
    </TabsPrimitive.Root>
  );
}
