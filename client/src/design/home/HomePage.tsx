/**
 * 主页：页头（标题与服务连接状态）、「开始创作」入口卡片、「我的模板」两个分组（云端在上、本地在下）。
 * 页面只负责排版与把用户操作交给上层；数据、加载状态与对话框由沙盒应用持有。
 */
import { cn } from "@/lib/utils";
import type { Library, TemplateSummary } from "../mock";
import { CreateCards } from "./CreateCards";
import { TemplateSection, type SectionState } from "./TemplateSection";

interface HomePageProps {
  desktop: boolean;
  /** 当前服务地址（主机与端口），显示在服务状态里。 */
  address: string;
  cloud: { state: SectionState; templates: TemplateSummary[] };
  local: { state: SectionState; templates: TemplateSummary[] };
  openId: string | null;
  dirty: boolean;
  onCreate: (library: Library) => void;
  onOpenEffects: () => void;
  onOpenSettings: () => void;
  onRefresh: (library: Library) => void;
  onOpen: (library: Library, template: TemplateSummary) => void;
  onDelete: (library: Library, template: TemplateSummary) => void;
  onCopyId: (template: TemplateSummary) => void;
}

/** 主页内容最宽 1400px，居中；窄窗口时边距收窄。 */
export function HomePage(props: HomePageProps) {
  const { desktop, address, cloud, local, openId, dirty, onCreate, onOpenEffects, onOpenSettings, onRefresh, onOpen, onDelete, onCopyId } = props;
  const sections: { library: Library; data: HomePageProps["cloud"] }[] = [
    { library: "cloud", data: cloud },
    { library: "local", data: local },
  ];
  return (
    <div className="mx-auto w-full max-w-[1400px] px-6 pb-20 pt-9 sm:px-10">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-[26px] font-semibold leading-tight tracking-tight">开始创作</h1>
          <p className="mt-1.5 text-sm text-fg-muted">从模板开始设计视频的样子，或者让 AI 帮你做一段字效。</p>
        </div>
        <ServiceChip state={cloud.state} address={address} onClick={onOpenSettings} />
      </header>

      <div className="mt-7">
        <CreateCards desktop={desktop} onCreateCloud={() => onCreate("cloud")} onCreateLocal={() => onCreate("local")} onOpenEffects={onOpenEffects} />
      </div>

      <div className="mt-14 flex items-baseline gap-3 border-b border-line pb-3">
        <h2 className="text-lg font-semibold tracking-tight">我的模板</h2>
        <span className="text-[13px] text-fg-subtle">点击卡片继续编辑</span>
      </div>

      <div className="mt-7 space-y-12">
        {sections.map(({ library, data }) => (
          <TemplateSection
            key={library}
            library={library}
            state={data.state}
            templates={data.templates}
            error={`连接 ${address} 超时（8 秒）`}
            openId={openId}
            dirty={dirty}
            onRefresh={() => onRefresh(library)}
            onCreate={() => onCreate(library)}
            onOpen={(template) => onOpen(library, template)}
            onDelete={(template) => onDelete(library, template)}
            onCopyId={onCopyId}
          />
        ))}
      </div>
    </div>
  );
}

/** 服务状态胶囊：由云端模板的读取结果推断连接情况，点击打开设置修改服务地址；不额外发起探测请求。 */
function ServiceChip({ state, address, onClick }: { state: SectionState; address: string; onClick: () => void }) {
  const status = {
    ready: { dot: "bg-success", label: "已连接" },
    loading: { dot: "bg-accent animate-pulse", label: "连接中" },
    error: { dot: "bg-danger", label: "连接失败" },
    unavailable: { dot: "bg-fg-subtle", label: "未连接" },
  }[state];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-label={`服务${status.label}：${address}，点击修改`}
      className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface pl-3 pr-3.5 text-xs text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
    >
      <span className={cn("size-1.5 rounded-full", status.dot)} />
      <span>{status.label}</span>
      <span className="font-mono text-[11px] text-fg-subtle">{address}</span>
    </button>
  );
}
