/** Remotion 工作区组合聊天、代码、预览和参数；服务状态与当前会话独立，页面卸载时清理。 */
import { cn } from "@/lib/utils";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { capabilities } from "./api";
import { ChatPanel } from "./ChatPanel";
import { CodePanel } from "./CodePanel";
import { ParametersPanel } from "./ParametersPanel";
import { PreviewPanel } from "./PreviewPanel";
import { useWorkHistory } from "./useWorkHistory";
import { HistorySidebar } from "./HistorySidebar";
import { useTemplateSession } from "./useTemplateSession";

/** 宽屏左右分栏，小屏切换聊天和预览但保留会话与播放器实例。 */
export function RemotionWorkspace() {
  const history = useWorkHistory();
  const session = useTemplateSession(history.refresh);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [mobile, setMobile] = useState("chat");
  const [serviceError, setServiceError] = useState("");
  const [check, setCheck] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void capabilities(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted)
          setServiceError(
            result.models_configured
              ? ""
              : "字效服务尚未配置模型，请先完成服务端配置。",
          );
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setServiceError(
            error instanceof Error ? error.message : "无法连接字效服务。",
          );
      });
    return () => controller.abort();
  }, [check]);
  const unresolved =
    !!session.job && ["queued", "running"].includes(session.job.status);
  const pending =
    session.loading ||
    !!session.busy ||
    session.dirty ||
    unresolved ||
    session.retryMode === "read";
  const locked = pending || previewBusy;
  return (
    <div className="space-y-4">
      <CodePanel
        key={`code-${session.key}`}
        code={session.code}
        pending={pending}
        onNew={session.reset}
      />
      {serviceError && (
        <div
          role="alert"
          className="flex items-center justify-between rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2 text-sm"
        >
          <span>{serviceError}</span>
          <Button variant="ghost" size="sm" onClick={() => setCheck(check + 1)}>
            重新连接
          </Button>
        </div>
      )}
      {session.error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 rounded-lg border px-4 py-2 text-sm"
        >
          <span>{session.error}</span>
          {session.retryMode && (
            <Button
              variant="outline"
              size="sm"
              disabled={!!session.busy}
              onClick={session.retry}
            >
              {session.retryMode === "read" ? "刷新任务" : "重试任务"}
            </Button>
          )}
        </div>
      )}
      {session.connection && (
        <p role="status" className="text-xs text-muted-foreground">
          {session.connection === "live"
            ? "会话已连接"
            : session.connection === "reconnecting"
              ? "连接中断，正在恢复更新；后台任务继续运行。"
              : "正在恢复会话…"}
        </p>
      )}
      <Tabs value={mobile} onValueChange={setMobile} className="lg:hidden">
        <TabsList aria-label="工作区面板">
          <TabsTrigger value="chat">聊天</TabsTrigger>
          <TabsTrigger value="preview">预览与参数</TabsTrigger>
        </TabsList>
      </Tabs>
      <div className="grid lg:h-[640px] min-h-0 gap-4 lg:h-[calc(100dvh-370px)] lg:min-h-[480px] lg:grid-cols-[210px_minmax(280px,2fr)_minmax(0,3fr)]">
        <HistorySidebar
          items={history.items}
          selected={session.workId}
          loading={history.loading}
          error={history.error}
          hasMore={!!history.next_cursor}
          onSelect={session.select}
          onRefresh={history.refresh}
          onMore={history.more}
        />
        <div
          className={cn(
            "h-[640px] min-h-0 lg:block lg:h-auto",
            mobile === "chat" ? "block" : "hidden",
          )}
        >
          <ChatPanel
            key={`chat-${session.key}`}
            messages={session.messages}
            job={session.job}
            hasOlder={!!session.nextBefore}
            olderLoading={session.olderLoading}
            onOlder={() => void session.older()}
            busy={!!session.busy}
            disabled={locked || !!serviceError}
            canStop={unresolved}
            first={!session.workId}
            onSend={(text, image) => {
              if (!locked) session.send(text, image);
            }}
            onStop={() => void session.stop()}
          />
        </div>
        <div
          className={cn(
            "h-[640px] min-h-0 lg:h-auto grid-rows-[minmax(280px,3fr)_minmax(180px,2fr)] gap-4 lg:grid",
            mobile === "preview" ? "grid" : "hidden",
          )}
        >
          <PreviewPanel
            key={`preview-${session.key}`}
            version={session.version}
            values={session.values}
            pending={pending}
            onBusyChange={setPreviewBusy}
          />
          <ParametersPanel
            version={session.version}
            values={session.values}
            disabled={
              locked ||
              session.job?.status === "needs_input" ||
              session.retryMode === "read"
            }
            pending={locked}
            onChange={(key, value) => {
              if (!locked) session.change(key, value);
            }}
          />
        </div>
      </div>
    </div>
  );
}
