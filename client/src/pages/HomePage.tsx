/** 正式客户端工作室：采用 PR #196 导航，切换页面时保留编辑草稿、播放器和会话订阅。 */
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
import { APP_VERSION } from "@/lib/version";

/** 首次访问才挂载编辑工作区，返回主页会重新读取模板列表。 */
export default function HomePage({ logoHidden = false }: { logoHidden?: boolean }) {
  const [page, setPage] = useState<Page>(() => restored.workspace === "library" ? "editor" : restored.workspace === "remotion" ? "effects" : restored.workspace === "fonts" ? "fonts" : "home");
  const [opened, setOpened] = useState({ editor: !!restored.template || restored.workspace === "library", effects: !!restored.remotionWork || restored.workspace === "remotion" });
  const [selection, setSelection] = useState<TemplateSelection | null>(restored.template ?? null);
  const [deletedTemplate, setDeletedTemplate] = useState<TemplateSelection | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 沿用已发布更新快照的工作区标识，重启后恢复模板与会话位置。
  useEffect(() => { recovery.workspace = page === "editor" ? "library" : page === "effects" ? "remotion" : page; }, [page]);
  function navigate(next: Page) {
    if (next === "editor" || next === "effects") setOpened((value) => ({ ...value, [next]: true }));
    setPage(next);
  }
  return <Tooltip.Provider delayDuration={350}>
    <div className="flex h-dvh overflow-hidden bg-canvas text-fg">
      <Rail page={page} onNavigate={navigate} onSettings={() => setSettingsOpen(true)} editing={editing?.name ?? null} dirty={editing?.dirty ?? false} version={APP_VERSION} logoHidden={logoHidden} />
      <main className="min-w-0 flex-1 overflow-auto">
        {page === "home" && <TemplateHome onDelete={setDeletedTemplate} editing={editing} onOpenSettings={() => setSettingsOpen(true)} onSelect={(next) => { setSelection(next); navigate("editor"); }} onOpenRemotion={() => navigate("effects")} />}
        <div hidden={page !== "editor"} className="h-full">{opened.editor && <TemplateWorkspace selection={selection} deletedTemplate={deletedTemplate} onHome={() => navigate("home")} onOpenRemotion={() => navigate("effects")} onEditingState={setEditing} active={page === "editor" && !settingsOpen} />}</div>
        <div hidden={page !== "effects"} className="h-full">{opened.effects && <RemotionWorkspace />}</div>
        {page === "fonts" && <div className="mx-auto max-w-[1400px] px-6 py-9 sm:px-10"><header className="mb-8"><h1 className="text-[26px] font-semibold tracking-tight">字体库</h1><p className="mt-1.5 text-sm text-fg-muted">查看可用字体，或上传自己的字体供字效使用。</p></header><FontLibrary /></div>}
      </main>
      <UpdateLifecycle />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  </Tooltip.Provider>;
}
