/** 工作区三栏布局：持久化用户尺寸，小屏切换显示但不卸载会话或播放器。 */
import { useEffect, useState, type ReactNode } from "react";
import { useGroupRef, type Layout } from "react-resizable-panels";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

const storageKey = "imv.remotion.layout";
const initialLayout = { history: 18, chat: 36, preview: 46 };
/** 忽略损坏或旧格式的布局；存储被禁用时仍使用默认尺寸。 */
function readLayout(): Layout {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? "null");
    if (
      value &&
      Object.keys(value).length === Object.keys(initialLayout).length &&
      Object.keys(initialLayout).every(
        (key) =>
          typeof value[key] === "number" &&
          Number.isFinite(value[key]) &&
          value[key] > 0 &&
          value[key] < 100,
      ) &&
      Math.abs(value.history + value.chat + value.preview - 100) < 0.1
    )
      return value;
  } catch {
    /* 本地偏好不可用不影响编辑。 */
  }
  return initialLayout;
}
/** 分栏库处理指针、键盘和拖动期间的面板事件屏蔽；恢复布局不重建子组件。 */
export function WorkspacePanels({
  history,
  chat,
  preview,
  mobile,
  status,
}: {
  history: ReactNode;
  chat: ReactNode;
  preview: ReactNode;
  mobile: string;
  status: ReactNode;
}) {
  const group = useGroupRef();
  const [layout] = useState(readLayout);
  const [desktop, setDesktop] = useState(
    () => window.matchMedia("(min-width: 1024px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const changed = () => setDesktop(query.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  /** 只保存有效尺寸，避免隐藏工作区或窄屏尺寸覆盖桌面偏好。 */
  function save(value: Layout) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      /* 存储不可用时仍允许拖动。 */
    }
  }
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex min-h-8 items-center justify-between gap-2 px-1">
        <div className="min-w-0 text-xs text-muted-foreground">{status}</div>
        <Button
          variant="ghost"
          size="sm"
          className="hidden h-7 shrink-0 text-xs text-muted-foreground lg:inline-flex"
          onClick={() => {
            group.current?.setLayout(initialLayout);
            save(initialLayout);
          }}
        >
          <RotateCcw className="size-3" />
          恢复布局
        </Button>
      </div>
      <ResizablePanelGroup
        groupRef={group}
        orientation="horizontal"
        disabled={!desktop}
        defaultLayout={layout}
        onLayoutChanged={(value, meta) => {
          if (desktop && meta.isUserInteraction) save(value);
        }}
        style={{ height: desktop ? "calc(100dvh - 190px)" : "auto" }}
        className="max-lg:block! lg:min-h-[560px]"
      >
        <ResizablePanel
          id="history"
          defaultSize="18%"
          minSize={180}
          maxSize="32%"
          className="min-w-0 max-lg:overflow-visible!"
        >
          {history}
        </ResizablePanel>
        <ResizableHandle
          aria-label="调整历史与聊天宽度"
          withHandle
          className="mx-1 hidden w-1.5 rounded-full bg-transparent transition-colors hover:bg-primary/15 lg:flex"
        />
        <ResizablePanel
          id="chat"
          defaultSize="36%"
          minSize={300}
          className={cn(
            "min-w-0 max-lg:mt-3 max-lg:h-[calc(100dvh-240px)] max-lg:min-h-[520px]",
            mobile !== "chat" && "max-lg:hidden!",
          )}
        >
          {chat}
        </ResizablePanel>
        <ResizableHandle
          aria-label="调整聊天与预览宽度"
          withHandle
          className="mx-1 hidden w-1.5 rounded-full bg-transparent transition-colors hover:bg-primary/15 lg:flex"
        />
        <ResizablePanel
          id="preview"
          defaultSize="46%"
          minSize={360}
          className={cn(
            "min-w-0 max-lg:mt-3 max-lg:h-[800px]",
            mobile !== "preview" && "max-lg:hidden!",
          )}
        >
          {preview}
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}
