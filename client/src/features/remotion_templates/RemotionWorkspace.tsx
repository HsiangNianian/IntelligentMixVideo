/** Remotion 工作区组合聊天、代码、预览和参数；服务状态与当前会话独立，页面卸载时清理。 */
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { capabilities } from "./api";
import { ChatPanel } from "./ChatPanel";
import { CompositionSettings } from "./CompositionSettings";
import { compositionSummary, resolveComposition } from "./composition";
import { WorkspacePanels } from "./WorkspacePanels";
import { useTemplateVersions } from "./useTemplateVersions";
import { ParametersPanel } from "./ParametersPanel";
import { PreviewPanel } from "./PreviewPanel";
import { useWorkHistory } from "./useWorkHistory";
import { HistorySidebar } from "./HistorySidebar";
import { useTemplateSession } from "./useTemplateSession";

/** 宽屏左右分栏，小屏切换聊天和预览但保留会话与播放器实例。 */
export function RemotionWorkspace() {
  const history = useWorkHistory();
  const session = useTemplateSession(history.refresh);
  const catalog = useTemplateVersions(session.workId, session.version);
  const viewed = session.previewVersion ?? session.version;
  const historical = !!session.previewVersion;
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
    unresolved ||
    session.retryMode === "read";
  const locked = pending || previewBusy || session.previewLoading;
  const configurationError =
    !session.workId && resolveComposition(session.compositionDraft).error;
  return (
    <div className="space-y-2">
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
              disabled={!session.canRecover}
              onClick={session.retry}
            >
              {session.retryMode === "read"
                ? "刷新任务"
                : session.retryMode === "version"
                  ? "重新读取结果"
                  : "重试任务"}
            </Button>
          )}
        </div>
      )}
      <Tabs value={mobile} onValueChange={setMobile} className="lg:hidden">
        <TabsList aria-label="工作区面板">
          <TabsTrigger value="chat">聊天</TabsTrigger>
          <TabsTrigger value="preview">预览与参数</TabsTrigger>
        </TabsList>
      </Tabs>
      <WorkspacePanels
        mobile={mobile}
        status={
          <span role="status">
            {session.connection === "reconnecting"
              ? "连接中断，正在恢复更新；后台任务继续运行。"
              : session.connection === "connecting"
                ? "正在恢复会话…"
                : "描述想法，预览字效，保留每一次灵感。"}
          </span>
        }
        history={
          <HistorySidebar
            items={history.items}
            selected={session.workId}
            loading={history.loading}
            error={history.error}
            hasMore={!!history.next_cursor}
            onSelect={session.select}
            onRefresh={history.refresh}
            onMore={history.more}
            onNew={session.reset}
          />
        }
        chat={
          <ChatPanel
            key={`chat-${session.key}`}
            messages={session.messages}
            job={session.job}
            jobs={session.jobs}
            versions={catalog.versions}
            latestVersionId={session.version?.id}
            previewVersionId={viewed?.id}
            code={session.code}
            versionDisabled={pending || session.dirty}
            previewDisabled={pending}
            onPreviewVersion={session.selectVersion}
            versionNotice={
              catalog.error ? (
                <div role="alert" className="text-xs text-destructive">
                  {catalog.error}
                  <Button variant="ghost" size="sm" onClick={catalog.retry}>
                    重试历史版本
                  </Button>
                </div>
              ) : undefined
            }
            hasOlder={!!session.nextBefore}
            olderLoading={session.olderLoading}
            onOlder={() => void session.older()}
            busy={!!session.busy}
            disabled={
              locked ||
              historical ||
              session.dirty ||
              !!serviceError ||
              !!configurationError
            }
            canStop={unresolved}
            first={!session.workId}
            configuration={
              !session.workId ? (
                <CompositionSettings
                  value={session.compositionDraft}
                  disabled={locked}
                  onChange={(value) => {
                    if (!locked) session.configure(value);
                  }}
                />
              ) : session.version ? (
                <p
                  className="text-xs text-muted-foreground"
                  aria-label="成功版本配置"
                >
                  {compositionSummary(session.version.spec.composition)}
                </p>
              ) : undefined
            }
            onSend={(text, image) => {
              if (!locked && !session.dirty) session.send(text, image);
            }}
            onStop={() => void session.stop()}
          />
        }
        preview={
          <div className="flex h-full min-h-0 flex-col gap-3">
            {(historical || session.previewLoading || session.previewError) && (
              <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-primary/15 bg-accent/50 px-3 py-2 text-xs">
                <span className="flex-1" role="status">
                  {session.previewLoading
                    ? "正在读取版本…"
                    : session.previewError ||
                      `正在查看 V${viewed?.number} · 历史版本只读`}
                </span>
                {session.version && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pending}
                    onClick={() => session.selectVersion(session.version!.id)}
                  >
                    返回最新版本
                  </Button>
                )}
              </div>
            )}
            <div className="grid min-h-0 flex-1 grid-rows-[minmax(280px,3fr)_minmax(180px,2fr)] gap-3">
              <PreviewPanel
                key={`preview-${session.key}`}
                version={viewed}
                values={
                  historical ? viewed!.candidate.default_config : session.values
                }
                pending={pending || session.previewLoading}
                onBusyChange={setPreviewBusy}
              />
              <ParametersPanel
                version={viewed}
                values={
                  historical ? viewed!.candidate.default_config : session.values
                }
                disabled={
                  locked ||
                  historical ||
                  session.job?.status === "needs_input" ||
                  session.retryMode === "read"
                }
                pending={locked}
                dirty={!historical && session.dirty}
                readOnly={historical}
                saving={session.busy === "parameters"}
                onSave={() => {
                  if (!locked) session.saveParameters();
                }}
                onDiscard={() => {
                  if (!locked) session.discardParameters();
                }}
                onChange={(key, value) => {
                  if (!locked) session.change(key, value);
                }}
              />
            </div>
          </div>
        }
      />
      <Dialog
        open={!!session.navigation}
        onOpenChange={(open) => {
          if (!open) session.resolveNavigation("cancel");
        }}
      >
        <DialogContent showCloseButton={!session.busy && !session.loading}>
          <DialogHeader>
            <DialogTitle>参数尚未保存</DialogTitle>
            <DialogDescription>
              保存配置后切换，或放弃本地修改。取消将继续编辑当前视图。
            </DialogDescription>
          </DialogHeader>
          {session.error && (
            <p role="alert" className="text-sm text-destructive">
              {session.error}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={!!session.busy || session.loading}
              onClick={() => session.resolveNavigation("cancel")}
            >
              取消
            </Button>
            <Button
              variant="outline"
              disabled={!!session.busy || session.loading}
              onClick={() => session.resolveNavigation("discard")}
            >
              放弃修改并切换
            </Button>
            <Button
              disabled={locked || session.retryMode === "read"}
              onClick={() => session.resolveNavigation("save")}
            >
              {session.navigation?.saving ? "正在保存…" : "保存并切换"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
