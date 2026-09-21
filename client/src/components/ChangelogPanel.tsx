/** 读取随客户端构建的仓库更新日志，以 Markdown 展示版本记录并提供独立滚动区域。 */
import { useId } from "react";
import Markdown from "react-markdown";
import changelog from "../../../CHANGELOG.md?raw";

/** 更新日志随源码构建更新；外部链接在新页面打开，保留当前工作区。 */
export function ChangelogPanel() {
  const headingId = useId();
  return (
    <aside aria-labelledby={headingId} className="flex h-full min-h-0 min-w-0 flex-col gap-5 py-3 sm:py-5">
      <div className="shrink-0 space-y-2 text-center">
        <h2 id={headingId} className="text-2xl font-semibold tracking-tight">更新日志</h2>
        <p className="text-sm text-muted-foreground">查看版本更新与功能改进。</p>
      </div>
      <div role="region" aria-label="版本更新内容" tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 py-5 text-sm leading-7 [overflow-wrap:anywhere] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&_h1]:mb-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:border-b [&_h2]:pb-2 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:font-semibold [&_p]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-2 [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-4 [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:text-xs [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-3 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_hr]:my-5 [&_img]:max-w-full">
        <Markdown components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>
          {changelog}
        </Markdown>
      </div>
    </aside>
  );
}
