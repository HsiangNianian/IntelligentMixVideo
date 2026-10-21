/**
 * 删除模板确认框：展示要删除的云端模板（缩略封面、名称与完整 ID），确认后才删除。
 * 删除进行中禁止关闭；失败时把原因留在框内并把按钮改为「重试删除」，取消则不做任何修改。
 * 真实请求失败保留诊断与删除目标；快速连点只发出一次请求。
 */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { Button, Spinner } from "../ui";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "../dialog";
import type { TemplateSummary } from "../types";
import { TemplateCover } from "./TemplateCard";

interface DeleteTemplateDialogProps {
  target: TemplateSummary | null;
  onDelete: (template: TemplateSummary) => Promise<void>;
  onClose: () => void;
}

type Phase = "idle" | "deleting" | "failed";

/** 删除确认框。 */
export function DeleteTemplateDialog({ target, onDelete, onClose }: DeleteTemplateDialogProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const locked = useRef(false);
  const active = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { setPhase("idle"); setError(""); }, [target]);

  const confirm = async () => {
    if (!target || locked.current) return;
    locked.current = true;
    setPhase("deleting");
    try {
      await onDelete(target);
      if (active.current) onClose();
    } catch (reason) {
      if (active.current) {
        setError(reason instanceof Error ? reason.message : "删除模板失败，请重试");
        setPhase("failed");
      }
    } finally { locked.current = false; }
  };

  // 关闭动画期间 target 已为 null，继续显示上一次的内容，避免框体先塌陷再淡出。
  const last = useRef(target);
  if (target) last.current = target;
  const template = target ?? last.current;
  const busy = phase === "deleting";
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent busy={busy}>
        <DialogHeader tone="danger" icon={<Icon name="trash-bin-minimalistic" variant="duotone" />} title="删除模板？" description="删除后不能恢复，已经用它合成的视频不受影响。" />
        {template && (
          <div className="px-6 pb-4 pt-4">
            <div className="flex items-center gap-4 rounded-xl border border-line bg-sunken p-3">
              <span className="w-14 shrink-0 overflow-hidden rounded-md ring-1 ring-line">
                <TemplateCover template={template} compact />
              </span>
              <div className="min-w-0 space-y-1">
                <p className="truncate text-sm font-medium">{template.name}</p>
                <p className="text-xs text-fg-muted">云端模板 · 团队成员都会失去它</p>
                <p className="select-all break-all font-mono text-[11px] text-fg-subtle">{template.template_id}</p>
              </div>
            </div>
            {phase === "failed" && (
              <p role="alert" className="mt-3 flex items-start gap-2 rounded-lg bg-danger/10 px-3 py-2.5 text-[13px] text-danger">
                <Icon name="danger-triangle" className="mt-px size-4 shrink-0" />
                {error}
              </p>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
          <Button variant="danger" disabled={busy} onClick={confirm}>
            {busy ? <><Spinner />正在删除</> : phase === "failed" ? "重试删除" : "确认删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
