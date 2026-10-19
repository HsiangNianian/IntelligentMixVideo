/**
 * 设计沙盒应用：组合导航栏、开场、主页、模板编辑、AI 字效与占位页，持有假数据、原型状态、服务地址与各对话框。
 * 所有「请求」都用定时器模拟，卸载时统一清理；接入正式客户端时，这里的状态会换成真实的 hooks 与 API。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { Toaster, toast } from "sonner";
import { APP_VERSION } from "@/lib/version";
import { Rail, type Page } from "./Rail";
import { BootScreen } from "./BootScreen";
import { Splash } from "./Splash";
import { PrototypePanel } from "./PrototypePanel";
import { SettingsDialog, defaultAddress } from "./SettingsDialog";
import { Button } from "./ui";
import { Icon } from "./icons";
import { HomePage } from "./home/HomePage";
import type { SectionState } from "./home/TemplateSection";
import { NewTemplateDialog } from "./home/NewTemplateDialog";
import { DeleteTemplateDialog } from "./home/DeleteTemplateDialog";
import { EditorPage } from "./editor/EditorPage";
import { EffectsPage } from "./effects/EffectsPage";
import { cloudTemplates, defaultProto, localTemplates, type Library, type ProtoState, type TemplateSummary } from "./mock";

/** 编辑器里打开的模板；新建的草稿还没有 ID。 */
interface OpenTemplate {
  id: string | null;
  name: string;
  description: string;
  library: Library;
}

/** 沙盒根组件。 */
export default function DesignApp() {
  const [page, setPage] = useState<Page>("home");
  const [proto, setProto] = useState<ProtoState>(defaultProto);
  const [lists, setLists] = useState<Record<Library, TemplateSummary[]>>({ cloud: cloudTemplates, local: localTemplates });
  const [refreshing, setRefreshing] = useState<Record<Library, boolean>>({ cloud: false, local: false });
  const [open, setOpen] = useState<OpenTemplate | null>({ id: cloudTemplates[0].template_id, name: cloudTemplates[0].name, description: cloudTemplates[0].description, library: "cloud" });
  const [creating, setCreating] = useState<Library | null>(null);
  // 服务地址：空字符串表示使用默认地址。
  const [address, setAddress] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleting, setDeleting] = useState<{ library: Library; template: TemplateSummary } | null>(null);
  // 每次打开沙盒都播放启动开场，原型面板可以重播。
  const [splash, setSplash] = useState(true);
  const hideSplash = useCallback(() => setSplash(false), []);
  const timers = useRef(new Set<number>());
  useEffect(() => () => timers.current.forEach((id) => window.clearTimeout(id)), []);
  // 配色写在 <html> 上，design.css 按 data-palette 覆盖颜色令牌。
  useEffect(() => {
    document.documentElement.dataset.palette = proto.palette;
  }, [proto.palette]);

  /** 按原型开关与模拟刷新得出分组状态；「空」用正常状态加空列表表示。 */
  const section = (library: Library): { state: SectionState; templates: TemplateSummary[] } => {
    if (library === "local" && proto.runtime === "browser") return { state: "unavailable", templates: [] };
    const mode = proto[library];
    if (refreshing[library] || mode === "loading") return { state: "loading", templates: [] };
    if (mode === "error") return { state: "error", templates: [] };
    return { state: "ready", templates: mode === "empty" ? [] : lists[library] };
  };

  /** 模拟重新读取：显示一秒加载；失败状态下重试视为恢复成功。 */
  const refresh = (library: Library) => {
    setRefreshing((value) => ({ ...value, [library]: true }));
    const id = window.setTimeout(() => {
      timers.current.delete(id);
      setRefreshing((value) => ({ ...value, [library]: false }));
      setProto((value) => (value[library] === "error" ? { ...value, [library]: "ready" } : value));
    }, 1100);
    timers.current.add(id);
  };

  const onDeleted = (library: Library, template: TemplateSummary) => {
    setLists((value) => ({ ...value, [library]: value[library].filter((item) => item.template_id !== template.template_id) }));
    if (open?.id === template.template_id) setOpen(null);
    setDeleting(null);
    toast.success(`已删除「${template.name}」`);
  };

  return (
    <MotionConfig reducedMotion="user">
      <TooltipPrimitive.Provider delayDuration={300} skipDelayDuration={150}>
        <div className="flex h-dvh overflow-hidden">
          <Rail
            page={page}
            onNavigate={setPage}
            onSettings={() => setSettingsOpen(true)}
            editing={open?.name ?? null}
            dirty={proto.dirty}
            version={APP_VERSION}
            logoHidden={splash}
          />
          <main className="min-w-0 flex-1 overflow-y-auto">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={page} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
                {page === "home" ? (
                  <HomePage
                    desktop={proto.runtime === "desktop"}
                    address={new URL(address || defaultAddress).host}
                    cloud={section("cloud")}
                    local={section("local")}
                    openId={open?.id ?? null}
                    dirty={proto.dirty}
                    onCreate={setCreating}
                    onOpenEffects={() => setPage("effects")}
                    onOpenSettings={() => setSettingsOpen(true)}
                    onRefresh={refresh}
                    onOpen={(library, template) => {
                      setOpen({ id: template.template_id, name: template.name, description: template.description, library });
                      setProto((value) => ({ ...value, dirty: false }));
                      setPage("editor");
                    }}
                    onDelete={(library, template) => setDeleting({ library, template })}
                    onCopyId={(template) => {
                      void navigator.clipboard?.writeText(template.template_id);
                      toast("已复制模板 ID", { description: template.template_id });
                    }}
                  />
                ) : page === "effects" ? (
                  <EffectsPage />
                ) : page === "editor" && open ? (
                  <EditorPage
                    key={open.id ?? open.name}
                    template={open}
                    dirty={proto.dirty}
                    onDirtyChange={(dirty) => setProto((value) => ({ ...value, dirty }))}
                    onHome={() => setPage("home")}
                  />
                ) : (
                  <Placeholder page={page} onHome={() => setPage("home")} />
                )}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>

        <NewTemplateDialog
          library={creating}
          onClose={() => setCreating(null)}
          onCreate={({ library, name, description }) => {
            setCreating(null);
            setOpen({ id: null, name, description, library });
            setProto((value) => ({ ...value, dirty: true }));
            setPage("editor");
          }}
        />
        <SettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          address={address}
          desktop={proto.runtime === "desktop"}
          onSave={(next) => {
            setAddress(next);
            setSettingsOpen(false);
            toast.success("已保存服务地址", { description: next || `使用默认地址 ${defaultAddress}` });
          }}
        />
        <DeleteTemplateDialog target={deleting} fails={proto.deleteFails} onClose={() => setDeleting(null)} onDeleted={onDeleted} />
        {proto.boot !== "off" && <BootScreen failed={proto.boot === "failed"} />}
        {/* 开场与内置服务启动同时进行：服务仍在启动时停在落版画面；启动失败则淡出露出失败页，不把标志交给导航栏。 */}
        {splash && <Splash holding={proto.boot === "starting"} handoff={proto.boot === "off"} onDone={hideSplash} />}
        <PrototypePanel value={proto} onChange={setProto} onReplaySplash={() => setSplash(true)} />
        <Toaster
          position="bottom-center"
          toastOptions={{
            unstyled: true,
            classNames: {
              toast: "flex w-full items-start gap-2.5 rounded-xl border border-line-strong bg-overlay px-4 py-3 text-[13px] text-fg shadow-pop",
              title: "font-medium",
              description: "mt-0.5 font-mono text-[11px] text-fg-subtle",
              icon: "mt-px text-success [&_svg]:size-4",
            },
          }}
        />
      </TooltipPrimitive.Provider>
    </MotionConfig>
  );
}

/** 占位页：没有打开模板时的编辑页，以及还没有重新设计的字体库。 */
function Placeholder({ page, onHome }: { page: Exclude<Page, "home" | "effects">; onHome: () => void }) {
  const content = {
    editor: { icon: <Icon name="clapperboard-edit" variant="duotone" />, title: "还没有打开模板", text: "回到主页选择一个模板，或新建一个。" },
    fonts: { icon: <Icon name="text-square" variant="duotone" />, title: "字体库", text: "这一页还没有重新设计，接入时先沿用现有页面，配色与控件换成新的令牌和基础组件。" },
  }[page];
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center px-6 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl border border-line bg-surface text-fg-muted [&_svg]:size-6">{content.icon}</span>
      <h1 className="mt-5 text-lg font-semibold tracking-tight">{content.title}</h1>
      <p className="mt-1.5 max-w-sm text-[13px] text-fg-muted">{content.text}</p>
      <Button size="sm" variant="ghost" className="mt-5" onClick={onHome}>
        <Icon name="arrow-left" />回到主页
      </Button>
    </div>
  );
}
