/** 读取随客户端构建的仓库更新日志，以时间轴样式的 Markdown 卡片展示版本记录并提供独立滚动区域。 */
import { useId } from "react";
import Markdown from "react-markdown";
import { History } from "lucide-react";
import changelog from "../../../CHANGELOG.md?raw";

/** 更新日志随源码构建更新；较宽屏幕居中标题，外部链接在新页面打开。 */
export function ChangelogPanel() {
  const headingId = useId();
  return (
    <aside aria-labelledby={headingId} className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_32px_-16px_rgb(0_0_0/0.12)]">
      <div className="flex shrink-0 items-center gap-3 border-b px-5 py-4 sm:justify-center">
        <span className="flex size-9 items-center justify-center rounded-xl bg-accent text-primary">
          <History className="size-[18px]" aria-hidden="true" />
        </span>
        <div>
          <h2 id={headingId} className="font-semibold tracking-tight">更新日志</h2>
          <p className="text-xs text-muted-foreground">查看版本更新与功能改进。</p>
        </div>
      </div>
      {/* 时间轴：一级版本标题画圆点，分类与条目共用左侧竖线；隐藏与面板标题重复的 Markdown 一级标题。 */}
      <div role="region" aria-label="版本更新内容" tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-6 pt-4 text-[13px] leading-6 [overflow-wrap:anywhere] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&_h1]:hidden [&_h1+p]:mb-2 [&_h1+p]:rounded-xl [&_h1+p]:bg-muted [&_h1+p]:px-3 [&_h1+p]:py-2 [&_h1+p]:text-xs [&_h1+p]:text-muted-foreground [&_h2]:relative [&_h2]:mt-5 [&_h2]:pb-1 [&_h2]:pl-6 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:before:absolute [&_h2]:before:left-0 [&_h2]:before:top-1.5 [&_h2]:before:size-3 [&_h2]:before:rounded-full [&_h2]:before:bg-primary [&_h2]:before:ring-4 [&_h2]:before:ring-accent [&_h2]:before:content-[''] [&_h2_a]:no-underline [&_h2_a]:text-foreground [&_h3]:ml-[5px] [&_h3]:border-l-2 [&_h3]:pb-1 [&_h3]:pl-[17px] [&_h3]:pt-3 [&_h3]:text-[11px] [&_h3]:font-semibold [&_h3]:uppercase [&_h3]:tracking-widest [&_h3]:text-primary [&_p]:my-3 [&_ul]:ml-[5px] [&_ul]:list-none [&_ul]:space-y-1.5 [&_ul]:border-l-2 [&_ul]:pb-1 [&_ul]:pl-[17px] [&_ol]:list-decimal [&_ol]:pl-5 [&_a]:text-primary [&_a]:underline-offset-4 [&_a:hover]:underline [&_code]:rounded-md [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[11px] [&_strong]:font-medium [&_strong]:text-foreground [&_em]:text-xs [&_em]:not-italic [&_em]:text-muted-foreground [&_em_a]:text-muted-foreground [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-3 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_hr]:my-5 [&_img]:max-w-full">
        <Markdown components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>
          {changelog}
        </Markdown>
      </div>
    </aside>
  );
}
