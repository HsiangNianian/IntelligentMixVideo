/** 字效三栏工作室：左侧对话、中间预览、右侧参数；栏宽可调整，隐藏与缩放不卸载播放器。 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useGroupRef, type Layout } from "react-resizable-panels";
import { History, PanelRightClose, PanelRightOpen, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

const storageKey = "imv.remotion.studio-layout";
const initialLayout = { chat: 36, preview: 39, parameters: 25 };

/** 布局偏好只在存储边界校验，沿用三栏工作室的尺寸。 */
function readLayout(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? "null");
    const keys = Object.keys(initialLayout);
    if (value && Object.keys(value).length === keys.length && keys.every((name) => typeof value[name] === "number" && Number.isFinite(value[name]) && value[name] > 0 && value[name] < 100) && Math.abs(keys.reduce((sum, name) => sum + value[name], 0) - 100) < 0.1) return value;
  } catch { /* 布局偏好损坏不影响工作区。 */ }
  return initialLayout;
}

/** 存储禁用时仍可调整当前窗口，不影响参数与会话保存。 */
function save(value: Layout) {
  try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* 保留当前布局。 */ }
}

/** 仅用户拖动时保存尺寸；恢复重置三栏宽度。 */
export function WorkspacePanels({ history, chat, preview, parameters, mobile, status, action }: {
  history: ReactNode; chat: ReactNode; preview: ReactNode; parameters: ReactNode;
  mobile: string; status: ReactNode; action: ReactNode;
}) {
  const group = useGroupRef();
  const parameterId = useId();
  const [parametersCollapsed, setParametersCollapsed] = useState(false);
  const [layout] = useState(readLayout);
  const expandedLayout = useRef(layout);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  const [historyOpen, setHistoryOpen] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const changed = () => setDesktop(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  // 窄屏参数位于预览下方，不沿用桌面的零宽度；内容始终挂载，保留输入和播放器。
  useEffect(() => { if (!desktop) group.current?.setLayout(expandedLayout.current); }, [desktop, group]);
  return <div className="flex min-h-0 flex-1 flex-col">
    <header className="paper-surface flex min-h-18 shrink-0 flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-3">
      <h1 aria-label="AI 字效" className="sketch-text mr-3 text-[28px] max-sm:sr-only">AI 字效</h1>
      <Button variant="ghost" size="sm" className="hidden lg:inline-flex" aria-expanded={historyOpen} aria-controls="effects-history" onClick={() => setHistoryOpen((value) => !value)}><History className="size-4" />历史会话</Button>
      <span className="min-w-0 flex-1 basis-32 truncate text-xs text-fg-subtle">{status}</span>
      <Button variant="ghost" size="sm" className="hidden lg:inline-flex" aria-expanded={!parametersCollapsed} aria-controls={parameterId} onClick={() => {
        const current = group.current?.getLayout();
        if (!current) return;
        // 收起时把参数宽度各分一半给对话与预览；展开恢复之前的三栏比例。
        group.current?.setLayout(parametersCollapsed ? expandedLayout.current : {
          chat: current.chat + current.parameters / 2,
          preview: current.preview + current.parameters / 2,
          parameters: 0,
        });
      }}>{parametersCollapsed ? <PanelRightOpen className="size-4" /> : <PanelRightClose className="size-4" />}{parametersCollapsed ? "展开参数" : "收起参数"}</Button>
      <Button variant="ghost" size="sm" className="ml-auto hidden text-xs text-fg-muted lg:inline-flex" onClick={() => {
        group.current?.setLayout(initialLayout);
        save(initialLayout);
      }}><RotateCcw className="size-3.5" />恢复布局</Button>
      <div className="ml-auto shrink-0">{action}</div>
    </header>
    <div className="relative flex min-h-0 flex-1 max-lg:flex-col">
      <div id="effects-history" className={cn("shrink-0 border-r border-line bg-surface lg:absolute lg:inset-y-0 lg:left-0 lg:z-30 lg:w-[240px] lg:shadow-pop", !historyOpen && "lg:hidden")}>{history}</div>
      <ResizablePanelGroup groupRef={group} orientation="horizontal" disabled={!desktop} defaultLayout={layout}
        onLayoutChanged={(value, meta) => {
          if (desktop && value.parameters > 0) {
            expandedLayout.current = value;
            if (meta.isUserInteraction) save(value);
          }
        }}
        className="min-w-0 flex-1 max-lg:block!" style={{ minHeight: desktop ? 0 : "auto" }}>
        <ResizablePanel id="chat" minSize={280} className={cn("min-w-0 max-lg:h-[calc(100dvh-150px)] max-lg:min-h-[500px]", mobile !== "chat" && "max-lg:hidden!")}>{chat}</ResizablePanel>
        <ResizableHandle aria-label="调整聊天与预览宽度" className="hidden w-px bg-line hover:bg-brand lg:flex" />
        <ResizablePanel id="preview" minSize={220} className={cn("min-w-0 max-lg:h-[600px]", mobile !== "preview" && "max-lg:hidden!")}>{preview}</ResizablePanel>
        <ResizableHandle aria-label="调整预览与参数宽度" className="hidden w-px bg-line hover:bg-brand lg:flex" />
        <ResizablePanel id="parameters" collapsible={desktop} collapsedSize={0} minSize={240}
          onResize={(size) => setParametersCollapsed(size.asPercentage === 0)} className={cn("min-w-0 max-lg:min-h-80", mobile !== "preview" && "max-lg:hidden!")}>
          <div id={parameterId} hidden={parametersCollapsed} className="h-full">{parameters}</div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  </div>;
}
