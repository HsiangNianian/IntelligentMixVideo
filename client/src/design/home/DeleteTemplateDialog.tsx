/**
 * 删除模板确认框：展示要删除的模板（缩略封面、名称、所属库与完整 ID），确认后才删除。
 * 删除进行中禁止关闭；失败时把原因留在框内并把按钮改为「重试删除」，取消则不做任何修改。
 * 原型里用定时器模拟请求，卸载与关闭时清理定时器。
 */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { Button, Spinner } from "../ui";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "../dialog";
import type { Library, TemplateSummary } from "../mock";
import { TemplateCover } from "./TemplateCard";

interface DeleteTemplateDialogProps {
  target: { library: Library; template: TemplateSummary } | null;
  /** 原型开关：为 true 时模拟删除失败。 */
  fails: boolean;
  onClose: () => void;
  onDeleted: (library: Library, template: TemplateSummary) => void;
}

type Phase = "idle" | "deleting" | "failed";

/** 删除确认框。 */
export function DeleteTemplateDialog({ target, fails, onClose, onDeleted }: DeleteTemplateDialogProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    setPhase("idle");
    return () => window.clearTimeout(timer.current);
  }, [target]);

  const confirm = () => {
    if (!target) return;
    setPhase("deleting");
    timer.current = window.setTimeout(() => {
      if (fails) setPhase("failed");
      else onDeleted(target.library, target.template);
    }, 900);
  };

  // 关闭动画期间 target 已为 null，继续显示上一次的内容，避免框体先塌陷再淡出。
  const last = useRef(target);
  if (target) last.current = target;
  const shown = target ?? last.current;
  const busy = phase === "deleting";
  const template = shown?.template;
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent busy={busy}>
        <DialogHeader tone="danger" icon={<Icon name="trash-bin-minimalistic" variant="duotone" />} title="删除这个模板？" description="删除后不能恢复，已经用它合成的视频不受影响。" />
        {template && (
          <div className="px-6 pb-4 pt-4">
            <div className="flex items-center gap-4 rounded-xl border border-line bg-sunken p-3">
              <span className="w-14 shrink-0 overflow-hidden rounded-md ring-1 ring-line">
                <TemplateCover template={template} compact />
              </span>
              <div className="min-w-0 space-y-1">
                <p className="truncate text-sm font-medium">{template.name}</p>
                <p className="text-xs text-fg-muted">{shown?.library === "cloud" ? "云端模板 · 团队成员都会失去它" : "本地模板 · 只影响这台电脑"}</p>
                <p className="select-all break-all font-mono text-[11px] text-fg-subtle">{template.template_id}</p>
              </div>
            </div>
            {phase === "failed" && (
              <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg bg-danger/10 px-3 py-2.5 text-[13px] text-danger">
                <Icon name="danger-triangle" className="mt-px size-4 shrink-0" />
                删除失败：服务暂时不可用（503），模板仍然保留，可以稍后重试。
              </p>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
          <Button variant="danger" disabled={busy} onClick={confirm}>
            {busy ? <><Spinner />正在删除</> : phase === "failed" ? "重试删除" : "删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
