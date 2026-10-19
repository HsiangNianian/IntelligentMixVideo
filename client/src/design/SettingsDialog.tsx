/**
 * 设置对话框：现在只有一项「服务地址」，另有「关于」区展示版本、构建校验码与运行环境。
 * 地址可以先「测试连接」再保存；留空表示使用默认地址。保存只提交真正改动的值，取消或关闭放弃草稿。
 * 原型里的测试连接用定时器模拟：本机与局域网地址视为连接成功，其余地址超时；关闭或卸载时清理定时器。
 */
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { APP_COMMIT, APP_VERSION } from "@/lib/version";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "./dialog";
import { Icon } from "./icons";
import { LogoMark } from "./Logo";
import { Button, Spinner, Tooltip } from "./ui";

/** 没有填写地址时使用的默认服务地址。 */
export const defaultAddress = "http://localhost:20070";

/** 地址只接受 http(s) 开头、不带账号密码的形式；末尾的斜杠去掉后保存。 */
function normalize(value: string): string | null {
  const text = value.trim().replace(/\/+$/, "");
  if (!text) return "";
  try {
    const url = new URL(text);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    return text;
  } catch {
    return null;
  }
}

type Probe = { state: "idle" } | { state: "testing" } | { state: "ok"; ms: number } | { state: "fail"; reason: string };

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 当前保存的地址；空字符串表示使用默认地址。 */
  address: string;
  onSave: (address: string) => void;
  /** 浏览器里只在当前页面保存，刷新后恢复默认。 */
  desktop: boolean;
}

/** 设置对话框本体；每次打开都从当前保存的地址开始。 */
export function SettingsDialog({ open, onOpenChange, address, onSave, desktop }: SettingsDialogProps) {
  const [draft, setDraft] = useState(address);
  const [probe, setProbe] = useState<Probe>({ state: "idle" });
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!open) return;
    setDraft(address);
    setProbe({ state: "idle" });
    setCopied(false);
    return () => window.clearTimeout(timer.current);
  }, [open, address]);

  const normalized = normalize(draft);
  const invalid = normalized === null;
  const changed = !invalid && normalized !== address;
  const effective = normalized || defaultAddress;

  const test = () => {
    if (invalid) return;
    setProbe({ state: "testing" });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const host = new URL(effective).hostname;
      const reachable = host === "localhost" || host === "127.0.0.1" || /^(192\.168|10)\./.test(host);
      setProbe(reachable ? { state: "ok", ms: 30 + Math.round(Math.random() * 40) } : { state: "fail", reason: "请求超时（8 秒）。请确认地址正确、服务已经启动，并且这台电脑能访问它。" });
    }, 800);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[min(520px,calc(100vw-32px))]">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!changed || normalized === null) return;
            onSave(normalized);
          }}
        >
          <DialogHeader icon={<Icon name="settings" variant="duotone" />} title="设置" description={desktop ? "设置保存在这台电脑上。" : "浏览器预览中只在当前页面保存，刷新后恢复默认。"} />

          <section className="space-y-2.5 px-6 pb-2 pt-5">
            <div className="flex items-baseline justify-between">
              <label htmlFor="settings-address" className="text-[13px] font-medium">服务地址</label>
              {normalized !== "" && (
                <button type="button" onClick={() => { setDraft(""); setProbe({ state: "idle" }); }} className="text-[11.5px] text-fg-subtle transition-colors hover:text-accent">恢复默认</button>
              )}
            </div>
            <div className="flex gap-2">
              <div className={cn("flex h-10 min-w-0 flex-1 items-center gap-2 rounded-[10px] border bg-sunken px-3 transition-[border-color,box-shadow] focus-within:border-accent/60 focus-within:ring-[3px] focus-within:ring-accent/15", invalid ? "border-danger/60" : "border-line")}>
                <Icon name="server-minimalistic" className="size-4 shrink-0 text-fg-subtle" />
                <input
                  id="settings-address"
                  value={draft}
                  autoFocus
                  spellCheck={false}
                  placeholder={defaultAddress}
                  aria-invalid={invalid || undefined}
                  aria-describedby="settings-address-help"
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setProbe({ state: "idle" });
                  }}
                  className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-fg outline-none placeholder:text-fg-subtle"
                />
              </div>
              <Button disabled={invalid || probe.state === "testing"} onClick={test}>
                {probe.state === "testing" ? <Spinner /> : <Icon name="restart" />}
                测试连接
              </Button>
            </div>
            <p id="settings-address-help" className={cn("min-h-[40px] text-[12px] leading-relaxed", invalid ? "text-danger" : "text-fg-subtle")}>
              {invalid ? "请填写 http:// 或 https:// 开头的地址，例如 http://192.168.1.20:20070" : <ProbeResult probe={probe} address={effective} empty={normalized === ""} />}
            </p>
          </section>

          <section className="mx-6 mb-4 flex items-center gap-3.5 rounded-2xl bg-raised/70 p-3.5">
            <LogoMark className="size-10 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-semibold">IntelligentMixVideo</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-fg-muted">
                <span>版本 {APP_VERSION}</span>
                <span aria-hidden="true">·</span>
                <span>{desktop ? "桌面客户端" : "浏览器预览"}</span>
              </p>
            </div>
            <Tooltip label="构建校验码：反馈问题时附上它，方便核对运行的是哪一版代码">
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(APP_COMMIT);
                  setCopied(true);
                }}
                className="flex h-7 shrink-0 items-center gap-1.5 rounded-lg border border-line bg-surface px-2 font-mono text-[11px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg [&_svg]:size-3.5"
              >
                {APP_COMMIT}
                <Icon name={copied ? "check-circle" : "copy"} />
              </button>
            </Tooltip>
          </section>

          <DialogFooter note={changed ? "保存后新的请求马上使用新地址；已打开的 AI 字效会话需要重启客户端后切换。" : undefined}>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
            <Button type="submit" variant="primary" disabled={!changed}>保存</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 地址下方的一行说明：未测试时说明默认值，测试中、成功、失败各有提示。 */
function ProbeResult({ probe, address, empty }: { probe: Probe; address: string; empty: boolean }) {
  switch (probe.state) {
    case "testing":
      return <span>正在连接 {address}…</span>;
    case "ok":
      return (
        <span className="flex items-center gap-1.5 text-success">
          <Icon name="check-circle" variant="duotone" className="size-4 shrink-0" />
          连接正常 · 响应 {probe.ms} ms
        </span>
      );
    case "fail":
      return (
        <span className="flex items-start gap-1.5 text-danger">
          <Icon name="danger-circle" variant="duotone" className="mt-px size-4 shrink-0" />
          无法连接：{probe.reason}
        </span>
      );
    default:
      return <span>{empty ? `留空时使用默认地址 ${defaultAddress}。` : "连接其他电脑或服务器上的服务时填写，例如 http://192.168.1.20:20070。"}</span>;
  }
}
