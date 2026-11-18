/** 正式客户端工作室：导航切换时保留编辑草稿、播放器和会话订阅。 */
import { useEffect, useState } from "react";
import { recovery, restored } from "@/features/updates/recovery";
import { UpdateLifecycle } from "@/features/updates/ClientUpdate";
import { Tooltip } from "radix-ui";
import { Rail, type Page } from "@/components/studio/Rail";
import { SettingsDialog } from "@/features/settings/SettingsDialog";
import { TemplateWorkspace, type EditingState } from "@/features/templates/TemplateWorkspace";
import { TemplateHome, type TemplateSelection } from "@/features/templates/TemplateHome";
import { RemotionWorkspace } from "@/features/remotion_templates/RemotionWorkspace";
import { FontLibrary } from "@/features/fonts/FontLibrary";
import { PlaygroundWorkspace } from "@/features/video_composition/PlaygroundWorkspace";
import { APP_VERSION } from "@/lib/version";

/** 保留主页列表和工作区草稿，模板成功保存后才更新列表版本。 */
export default function HomePage({ logoHidden = false }: { logoHidden?: boolean }) {
  const [page, setPage] = useState<Page>(() => restored.workspace === "library" ? "editor" : restored.workspace === "remotion" ? "effects" : restored.workspace === "fonts" ? "fonts" : restored.workspace === "playground" ? "playground" : "home");
  const [opened, setOpened] = useState({ home: page === "home", editor: !!restored.template || restored.workspace === "library", effects: !!restored.remotionWork || restored.workspace === "remotion" });
  const [selection, setSelection] = useState<TemplateSelection | null>(restored.template ?? null);
  const [deletedTemplate, setDeletedTemplate] = useState<TemplateSelection | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [templateRevision, setTemplateRevision] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [playgroundOpened, setPlaygroundOpened] = useState(restored.workspace === "playground");
  // 沿用已发布更新快照的工作区标识，重启后恢复模板与会话位置。
  useEffect(() => { recovery.workspace = page === "editor" ? "library" : page === "effects" ? "remotion" : page; }, [page]);
  function navigate(next: Page) {
    if (next === "home" || next === "editor" || next === "effects") setOpened((value) => ({ ...value, [next]: true }));
    if (next === "playground") setPlaygroundOpened(true);
    setPage(next);
  }
  return <Tooltip.Provider delayDuration={350}>
    <div className="flex h-dvh overflow-hidden bg-canvas text-fg">
      <Rail page={page} onNavigate={navigate} onSettings={() => setSettingsOpen(true)} editing={editing?.name ?? null} dirty={editing?.dirty ?? false} version={APP_VERSION} logoHidden={logoHidden} />
      <main className="min-w-0 flex-1 overflow-auto">
        <div hidden={page !== "home"} className="h-full">{opened.home && <TemplateHome active={page === "home" && !settingsOpen} revision={templateRevision} onDelete={setDeletedTemplate} editing={editing} onOpenSettings={() => setSettingsOpen(true)} onSelect={(next) => { setSelection(next); navigate("editor"); }} onOpenRemotion={() => navigate("effects")} />}</div>
        <div data-update-workspace="editor" hidden={page !== "editor"} className="h-full">{opened.editor && <TemplateWorkspace selection={selection} deletedTemplate={deletedTemplate} onHome={() => navigate("home")} onOpenRemotion={() => navigate("effects")} onEditingState={setEditing} onSaved={() => setTemplateRevision((value) => value + 1)} active={page === "editor" && !settingsOpen} />}</div>
        <div data-update-workspace="effects" hidden={page !== "effects"} className="h-full">{opened.effects && <RemotionWorkspace />}</div>
        <div hidden={page !== "playground"} className="h-full">{playgroundOpened && <PlaygroundWorkspace active={page === "playground"} />}</div>
        {page === "fonts" && <FontLibrary />}
      </main>
      <UpdateLifecycle onReview={field => {
        if (field.closest('[role="dialog"]')) return;
        setSettingsOpen(false);
        const workspace = field.closest<HTMLElement>('[data-update-workspace]')?.dataset.updateWorkspace;
        if (workspace === "editor" || workspace === "effects") navigate(workspace);
      }} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  </Tooltip.Provider>;
}
