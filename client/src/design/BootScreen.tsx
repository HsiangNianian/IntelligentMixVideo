/**
 * 启动页：桌面客户端等待内置服务就绪时覆盖整个窗口。启动中显示标志与不确定进度条；
 * 失败时显示服务给出的原始诊断，并提供复制，方便发给技术同事。
 */
import { useState } from "react";
import { Button } from "./ui";
import { Icon } from "./icons";
import { LogoMark } from "./Logo";

/** 原型里展示的诊断样例。 */
const sampleError = `内置服务启动失败：MySQL 初始化超时（120 秒）
  at server.desktop.supervise (desktop.py:214)
  数据目录：~/.local/share/IntelligentMixVideo/backend/mysql`;

/** 启动页本体；failed 切换为失败状态。 */
export function BootScreen({ failed }: { failed: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas px-6">
      <div className="absolute inset-x-0 top-0 h-[60vh] bg-[radial-gradient(ellipse_at_top,color-mix(in_oklch,var(--color-accent)_9%,transparent),transparent_70%)]" />
      {failed ? (
        <div role="alert" className="relative w-full max-w-lg animate-pop-in rounded-[20px] border border-line-strong bg-surface p-6 shadow-pop">
          <span className="flex size-11 items-center justify-center rounded-xl bg-danger/15 text-danger">
            <Icon name="danger-triangle" variant="duotone" className="size-5" />
          </span>
          <h1 className="mt-4 text-lg font-semibold tracking-tight">内置服务没有启动成功</h1>
          <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">请把下面的诊断信息发给技术同事。也可以在设置里改为连接已有的服务地址。</p>
          <pre className="mt-4 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-sunken p-3.5 font-mono text-[11.5px] leading-relaxed text-fg-muted">{sampleError}</pre>
          <div className="mt-5 flex justify-end">
            <Button
              size="sm"
              onClick={() => {
                void navigator.clipboard?.writeText(sampleError);
                setCopied(true);
              }}
            >
              <Icon name={copied ? "check-circle" : "copy"} />
              {copied ? "已复制" : "复制诊断信息"}
            </Button>
          </div>
        </div>
      ) : (
        <div role="status" className="relative flex flex-col items-center text-center">
          <LogoMark className="size-14" />
          <h1 className="mt-6 text-base font-medium">正在启动内置服务</h1>
          <p className="mt-1.5 text-[13px] text-fg-muted">第一次运行需要初始化数据库，可能要等一两分钟。</p>
          <span className="mt-6 block h-[3px] w-48 overflow-hidden rounded-full bg-raised">
            <span className="block h-full w-1/3 animate-sweep rounded-full bg-accent" />
          </span>
        </div>
      )}
    </div>
  );
}
