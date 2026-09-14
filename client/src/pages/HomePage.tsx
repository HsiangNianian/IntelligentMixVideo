/** 首页保留时钟并提供模板库、Remotion 字效两个入口；各功能独立维护状态和资源。 */
import { useState } from "react";
import CurrentTime from "@/components/CurrentTime";
import { TemplateWorkspace } from "@/features/templates/TemplateWorkspace";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** 字效页签隐藏时保留会话与 SSE 订阅；离开首页时才清理整个工作区。 */
export default function HomePage() {
  const [workspace, setWorkspace] = useState("remotion");
  return (
    <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-5 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <p className="text-xs font-medium tracking-widest text-muted-foreground">
            INTELLIGENT MIX VIDEO
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">特效模板</h1>
        </div>
        <CurrentTime />
      </header>
      <Tabs value={workspace} onValueChange={setWorkspace}>
        <TabsList aria-label="模板工作区">
          <TabsTrigger value="library">模板库</TabsTrigger>
          <TabsTrigger value="remotion">Remotion 字效</TabsTrigger>
        </TabsList>
        <TabsContent value="library">
          <TemplateWorkspace />
        </TabsContent>
        <TabsContent
          value="remotion"
          forceMount
          hidden={workspace !== "remotion"}
        >
          <RemotionWorkspace />
        </TabsContent>
      </Tabs>
    </main>
  );
}
