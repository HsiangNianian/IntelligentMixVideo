/** 四栏工作室：历史可收起，聊天、预览、参数分别滚动；切换布局不卸载播放器。 */
import { useEffect, useState, type ReactNode } from "react";
import { useGroupRef, type Layout } from "react-resizable-panels";
import { History, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

const storageKey = "imv.remotion.studio-layout";
const initialLayout = { chat: 36, preview: 39, parameters: 25 };
/** 新布局使用独立偏好键，拒绝旧三栏或损坏的尺寸。 */
function readLayout(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? "null");
    const keys = Object.keys(initialLayout);
    if (value && Object.keys(value).length === keys.length && keys.every((key) => typeof value[key] === "number" && Number.isFinite(value[key]) && value[key] > 0 && value[key] < 100) && Math.abs(keys.reduce((sum, key) => sum + value[key], 0) - 100) < 0.1) return value;
  } catch { /* 布局偏好损坏不影响工作区。 */ }
  return initialLayout;
}
/** 仅用户拖动时保存尺寸；隐藏页面与窗口缩放不覆盖偏好。 */
export function WorkspacePanels({ history, chat, preview, parameters, mobile, status, action }: {
  history: ReactNode; chat: ReactNode; preview: ReactNode; parameters: ReactNode;
  mobile: string; status: ReactNode; action: ReactNode;
}) {
  const group = useGroupRef();
  const [layout] = useState(readLayout);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  const [historyOpen, setHistoryOpen] = useState(() => window.innerWidth >= 1440);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const changed = () => setDesktop(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  function save(value: Layout) {
    try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* 存储禁用时仍可拖动。 */ }
  }
  return <div className="flex min-h-0 flex-1 flex-col">
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-surface px-3">
      <Button variant="ghost" size="sm" className="hidden lg:inline-flex" aria-expanded={historyOpen} aria-controls="effects-history" onClick={() => setHistoryOpen((value) => !value)}><History className="size-4" />历史会话</Button>
      <span className="hidden min-w-0 flex-1 truncate text-xs text-fg-subtle sm:block">{status}</span>
      <Button variant="ghost" size="sm" className="ml-auto hidden text-xs text-fg-muted lg:inline-flex" onClick={() => { group.current?.setLayout(initialLayout); save(initialLayout); }}><RotateCcw className="size-3.5" />恢复布局</Button>
      <div className="ml-auto shrink-0">{action}</div>
    </header>
    <div className="flex min-h-0 flex-1 max-lg:flex-col">
      <div id="effects-history" className={cn("shrink-0 border-r border-line bg-surface lg:w-[216px]", !historyOpen && "lg:hidden")}>{history}</div>
      <ResizablePanelGroup groupRef={group} orientation="horizontal" disabled={!desktop} defaultLayout={layout}
        onLayoutChanged={(value, meta) => { if (desktop && meta.isUserInteraction) save(value); }}
        className="min-w-0 flex-1 max-lg:block!" style={{ minHeight: desktop ? 0 : "auto" }}>
        <ResizablePanel id="chat" minSize={280} className={cn("min-w-0 max-lg:h-[calc(100dvh-150px)] max-lg:min-h-[500px]", mobile !== "chat" && "max-lg:hidden!")}>{chat}</ResizablePanel>
        <ResizableHandle aria-label="调整聊天与预览宽度" className="hidden w-px bg-line hover:bg-brand lg:flex" />
        <ResizablePanel id="preview" minSize={220} className={cn("min-w-0 max-lg:h-[650px]", mobile !== "preview" && "max-lg:hidden!")}>{preview}</ResizablePanel>
        <ResizableHandle aria-label="调整预览与参数宽度" className="hidden w-px bg-line hover:bg-brand lg:flex" />
        <ResizablePanel id="parameters" minSize={220} maxSize="40%" className={cn("min-w-0 max-lg:min-h-80", mobile !== "preview" && "max-lg:hidden!")}>{parameters}</ResizablePanel>
      </ResizablePanelGroup>
    </div>
  </div>;
}
