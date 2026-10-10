/** 首页以侧边导航组合工作区；设置经侧栏底部按钮打开对话框，业务面板隐藏时保留草稿、播放器与订阅。 */
import { Fragment, useId, useState } from "react";
import { Film, House, PanelLeftClose, PanelLeftOpen, Settings, Sparkles, SquarePen } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Hint } from "@/components/Hint";
import { Button } from "@/components/ui/button";
import { SettingsDialog } from "@/features/settings/SettingsDialog";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { TemplateHome, type TemplateSelection } from "@/features/templates/TemplateHome";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { Tabs as TabsPrimitive } from "radix-ui";
import { TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/** 工作区固定入口共享侧栏样式；保留文字的无障碍名称，窄屏只显示图标。 */
const navigation = [
  { value: "home", label: "主页", icon: House },
  { value: "library", label: "模版编辑", icon: SquarePen },
  { value: "remotion", label: "Remotion 字效", icon: Sparkles },
];

/** 工作区首次打开后仅隐藏；根使用 Radix 避免竖向 group 样式影响嵌套横向标签。 */
export default function HomePage() {
  const [workspace, setWorkspace] = useState("home");
  const [libraryOpened, setLibraryOpened] = useState(false);
  const [remotionOpened, setRemotionOpened] = useState(false);
  const [selection, setSelection] = useState<TemplateSelection | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 侧栏收起只调整导航宽度，工作区继续保留当前编辑状态。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const navigationId = useId();
  return (
    <TabsPrimitive.Root
      orientation="vertical"
      value={workspace}
      onValueChange={(value) => {
        // 模板库首次访问才加载 SDK；之后切换不卸载未保存的编辑状态。
        if (value === "library") setLibraryOpened(true);
        if (value === "remotion") setRemotionOpened(true);
        setWorkspace(value);
      }}
      className="flex min-h-dvh gap-0"
    >
      <aside className={cn("sticky top-0 flex h-dvh w-16 shrink-0 flex-col border-r bg-sidebar px-2 py-4 transition-[width,padding] duration-200 motion-reduce:transition-none", !sidebarCollapsed && "md:w-56 md:px-3")}>
        <div className={cn("mb-8 flex h-10 items-center justify-center gap-2.5", !sidebarCollapsed && "md:justify-start md:px-1.5")}>
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
            <Film className="size-[18px]" strokeWidth={2} aria-hidden="true" />
          </div>
          <div className={cn("hidden min-w-0", !sidebarCollapsed && "md:block")}>
            <p className="truncate text-sm font-semibold tracking-tight">IntelligentMix</p>
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Video Studio</p>
          </div>
        </div>
        <TabsList id={navigationId} aria-label="模板工作区" className="flex w-full flex-1 flex-col items-stretch justify-start gap-1 rounded-none bg-transparent p-0">
          {navigation.map(({ value, label, icon: Icon }) => (
            <Fragment key={value}>
              {value === "library" && (
                <p className={cn("mb-1 mt-5 hidden px-3 text-[11px] font-medium tracking-wider text-muted-foreground/70", !sidebarCollapsed && "md:block")}>工作空间</p>
              )}
              <Hint label={label} side="right" visibleBelow={sidebarCollapsed ? undefined : "md"}><div className="flex"><TabsTrigger
                value={value}
                className={cn("h-9 w-full flex-none justify-center gap-3 rounded-md px-3 py-2 font-normal text-muted-foreground hover:bg-accent/60 hover:text-foreground data-[state=active]:bg-accent data-[state=active]:font-medium data-[state=active]:text-foreground before:absolute before:inset-y-2 before:-left-2 before:w-[3px] before:rounded-r-full before:bg-primary before:opacity-0 before:transition-opacity data-[state=active]:before:opacity-100 data-[state=active]:[&_svg]:text-primary dark:data-[state=active]:border-transparent dark:data-[state=active]:bg-accent", !sidebarCollapsed && "md:justify-start md:before:-left-3")}
              >
                <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden="true" />
                <span className={cn("sr-only", !sidebarCollapsed && "md:not-sr-only")}>{label}</span>
              </TabsTrigger></div></Hint>
            </Fragment>
          ))}
        </TabsList>
        <div className="space-y-1 border-t pt-3">
          <ThemeToggle
            collapsed={sidebarCollapsed}
            className={cn("h-9 w-full justify-center gap-3 rounded-md px-3 font-normal text-muted-foreground hover:bg-accent/60 hover:text-foreground", !sidebarCollapsed && "md:justify-start")}
          />
          <Hint label="设置" side="right" visibleBelow={sidebarCollapsed ? undefined : "md"}><Button
            type="button"
            variant="ghost"
            aria-haspopup="dialog"
            onClick={() => setSettingsOpen(true)}
            className={cn("h-9 w-full justify-center gap-3 rounded-md px-3 font-normal text-muted-foreground hover:bg-accent/60 hover:text-foreground", !sidebarCollapsed && "md:justify-start")}
          >
            <Settings className="size-[18px]" strokeWidth={1.75} aria-hidden="true" />
            <span className={cn("sr-only", !sidebarCollapsed && "md:not-sr-only")}>设置</span>
          </Button></Hint>
          <Hint label="展开侧边栏" side="right" disabled={!sidebarCollapsed}><Button
            type="button"
            variant="ghost"
            aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            aria-expanded={!sidebarCollapsed}
            aria-controls={navigationId}
            onClick={() => setSidebarCollapsed((value) => !value)}
            className={cn("hidden h-9 w-full shrink-0 cursor-pointer justify-center gap-3 rounded-md px-3 font-normal text-muted-foreground hover:bg-accent/60 hover:text-foreground md:inline-flex", !sidebarCollapsed && "md:justify-start")}
          >
            {sidebarCollapsed ? <PanelLeftOpen className="size-[18px]" aria-hidden="true" /> : <PanelLeftClose className="size-[18px]" aria-hidden="true" />}
            {!sidebarCollapsed && <span>收起侧边栏</span>}
          </Button></Hint>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-3 py-3 sm:px-6">
        <div className="mx-auto max-w-[1920px] space-y-3">
          {workspace === "remotion" && <header className="flex h-14 shrink-0 items-center gap-3 border-b px-1">
            {/* Remotion 页头只保留标题与说明；主页标题由模板主页自身提供，品牌标识统一放在侧栏。 */}
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold tracking-tight">Remotion 字效</h1>
              <p className="truncate text-xs text-muted-foreground">用对话生成可调参数的文字动效</p>
            </div>
          </header>}
          <TabsContent value="home" forceMount hidden={workspace !== "home"}>
            {workspace === "home" && (
              <TemplateHome
                onSelect={(next) => {
                  setSelection(next);
                  setLibraryOpened(true);
                  setWorkspace("library");
                }}
                onOpenRemotion={() => {
                  setRemotionOpened(true);
                  setWorkspace("remotion");
                }}
              />
            )}
          </TabsContent>
          <TabsContent value="library" forceMount hidden={workspace !== "library"}>
            {libraryOpened && <TemplateWorkspace selection={selection} onHome={() => setWorkspace("home")} />}
          </TabsContent>
          <TabsContent value="remotion" forceMount hidden={workspace !== "remotion"}>
            {remotionOpened && <RemotionWorkspace />}
          </TabsContent>
        </div>
      </main>
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </TabsPrimitive.Root>
  );
}
