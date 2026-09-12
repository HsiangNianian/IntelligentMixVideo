/** 模板工作区：协调列表、草稿、保存和切换保护；数据通信及 SDK 生命周期分别封装。 */
import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import * as api from "./api";
import {
  newDraft,
  toDraft,
  type Draft,
  type EffectAsset,
  type Template,
} from "./model";
import { EffectEditor } from "./EffectEditor";
import { TemplatePreview } from "./TemplatePreview";

/** 对话框只保存当前操作所需状态；null target 表示切换到新建。 */
type Action =
  | { type: "switch"; target: string | null }
  | { type: "rename" | "copy" | "delete" };

/** 使用独立草稿，只有成功保存或明确放弃才替换；所有写入串行，防止双击提交。 */
export function TemplateWorkspace() {
  const id = useId();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [current, setCurrent] = useState<Template | null>(null);
  const [draft, setDraft] = useState<Draft>(newDraft);
  const [baseline, setBaseline] = useState(() => JSON.stringify(newDraft()));
  const [catalog, setCatalog] = useState<EffectAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [newName, setNewName] = useState("");
  const lock = useRef(false);
  const dirty = JSON.stringify(draft) !== baseline;

  useEffect(() => {
    const controller = new AbortController();
    void api
      .listTemplates(controller.signal)
      .then(setTemplates)
      .catch((error) => {
        if (!controller.signal.aborted)
          setError(error instanceof Error ? error.message : "模板列表加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** 替换当前草稿并记录保存基线；仅在成功读取、保存或明确放弃时调用。 */
  function adopt(template: Template | null) {
    const next = template ? toDraft(template) : newDraft();
    setCurrent(template);
    setDraft(next);
    setBaseline(JSON.stringify(next));
  }

  /** 所有操作共用互斥与错误展示，失败不会清空草稿。 */
  async function perform(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  /** 保存成功后直接回填响应，避免额外刷新失败被误认为保存失败。 */
  async function persist(value = draft, templateId = current?.template_id) {
    const saved = await api.saveTemplate(value, templateId);
    setTemplates((items) => [
      saved,
      ...items.filter((item) => item.template_id !== saved.template_id),
    ]);
    adopt(saved);
    setNotice(`模板「${saved.name}」已保存`);
  }

  /** 先获取目标详情再替换，加载失败仍停留在原模板。 */
  async function switchTo(target: string | null) {
    adopt(target ? await api.getTemplate(target) : null);
    setAction(null);
  }

  /** 脏状态必须经用户选择，干净状态直接加载目标。 */
  function requestSwitch(target: string | null) {
    if (target && target === current?.template_id) return;
    if (dirty) {
      setError("");
      setAction({ type: "switch", target });
    } else void perform(() => switchTo(target));
  }

  /** 重命名和另存为包含当前草稿；删除只针对已保存 ID。 */
  async function confirmAction() {
    if (!action) return;
    if (action.type === "delete" && current) {
      await api.deleteTemplate(current.template_id);
      setTemplates((items) =>
        items.filter((item) => item.template_id !== current.template_id),
      );
      adopt(null);
      setNotice("模板已删除");
    } else if (action.type === "copy" || action.type === "rename") {
      // 另存为显式无 ID，不能落入 persist 的当前模板默认参数。
      const saved = await api.saveTemplate(
        { ...draft, name: newName },
        action.type === "copy" ? undefined : current?.template_id,
      );
      setTemplates((items) => [
        saved,
        ...items.filter((item) => item.template_id !== saved.template_id),
      ]);
      adopt(saved);
      setNotice(`模板「${saved.name}」已保存`);
    }
    setAction(null);
  }

  return (
    <div className="grid items-start gap-6 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)]">
      {/* 左侧集中模板选择与编辑，右侧预览始终从工作区顶部开始。 */}
      <section aria-label="模板配置" className="min-w-0 space-y-5">
        <Card className="gap-4 p-5">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-48 flex-1 space-y-2">
              <Label htmlFor={`${id}-picker`}>打开模板</Label>
              <Select
                value={current?.template_id || "new"}
                onValueChange={(value) =>
                  requestSwitch(value === "new" ? null : value)
                }
                disabled={busy || loading}
              >
                <SelectTrigger id={`${id}-picker`} className="w-full">
                  <SelectValue placeholder="新建模板" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new">新建模板</SelectItem>
                  {templates.map((item) => (
                    <SelectItem key={item.template_id} value={item.template_id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => requestSwitch(null)}
            >
              新建模板
            </Button>
            <Button
              variant="outline"
              disabled={busy || loading}
              onClick={() =>
                void perform(async () => {
                  setTemplates(await api.listTemplates());
                  setNotice("模板列表已刷新，当前编辑内容已保留");
                })
              }
            >
              刷新列表
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {loading
              ? "正在读取模板库…"
              : `共享模板库 · ${templates.length} 个模板`}
            {dirty ? " · 有未保存的修改" : ""}
          </p>
        </Card>
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
          >
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="text-sm text-muted-foreground">
            {notice}
          </p>
        )}
        <Card className="gap-5 p-5">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void perform(() => persist());
            }}
          >
            <fieldset
              disabled={busy}
              className="min-w-0 space-y-5 disabled:opacity-60"
            >
              <div className="space-y-2">
                <Label htmlFor={`${id}-name`}>模板名称</Label>
                <Input
                  id={`${id}-name`}
                  required
                  maxLength={100}
                  placeholder="为这组效果命名"
                  value={draft.name}
                  onChange={(event) =>
                    setDraft({ ...draft, name: event.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${id}-description`}>模板说明</Label>
                <Textarea
                  id={`${id}-description`}
                  maxLength={1000}
                  placeholder="描述风格或适用场景（可选）"
                  value={draft.description}
                  onChange={(event) =>
                    setDraft({ ...draft, description: event.target.value })
                  }
                />
              </div>
              <EffectEditor
                draft={draft}
                catalog={catalog}
                onChange={setDraft}
              />
              <div className="flex flex-wrap gap-2 border-t pt-5">
                {/* 本地编辑不等待模板库；保存等待初次读取，避免晚到列表覆盖保存结果。 */}
                <Button type="submit" disabled={loading}>
                  {busy ? "正在处理…" : "保存模板"}
                </Button>
                {current &&
                  (
                    [
                      ["rename", "重命名"],
                      ["copy", "另存为"],
                      ["delete", "删除"],
                    ] as const
                  ).map(([type, label]) => (
                    <Button
                      key={type}
                      type="button"
                      variant={type === "delete" ? "destructive" : "outline"}
                      onClick={() => {
                        setNewName(type === "rename" ? draft.name : "");
                        setError("");
                        setAction({ type });
                      }}
                    >
                      {label}
                    </Button>
                  ))}
              </div>
              {current && (
                <p className="break-all text-xs text-muted-foreground">
                  最近保存：{new Date(current.updated_at).toLocaleString()}
                  <br />
                  ID：{current.template_id}
                </p>
              )}
            </fieldset>
          </form>
        </Card>
      </section>
      <TemplatePreview draft={draft} onCatalog={setCatalog} />
      <Dialog
        open={!!action}
        onOpenChange={(open) => {
          if (!open && !busy) setAction(null);
        }}
      >
        <DialogContent showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>
              {action?.type === "switch"
                ? "保存当前修改？"
                : action?.type === "delete"
                  ? "删除模板？"
                  : action?.type === "copy"
                    ? "另存为新模板"
                    : "重命名模板"}
            </DialogTitle>
            <DialogDescription>
              {action?.type === "switch"
                ? "切换前可以保存当前配置、放弃修改，或取消切换。"
                : action?.type === "delete"
                  ? "删除后无法恢复，当前模板的未保存修改也会丢失。"
                  : "将保存当前完整配置；模板名称不能与其他模板重复。"}
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void perform(confirmAction);
            }}
            className="space-y-4"
          >
            {(action?.type === "rename" || action?.type === "copy") && (
              <div className="space-y-2">
                <Label htmlFor={`${id}-new-name`}>新名称</Label>
                <Input
                  id={`${id}-new-name`}
                  required
                  maxLength={100}
                  disabled={busy}
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                />
              </div>
            )}
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setAction(null)}
              >
                取消
              </Button>
              {action?.type === "switch" ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void perform(() => switchTo(action.target))}
                  >
                    放弃修改
                  </Button>
                  <Button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void perform(async () => {
                        await persist();
                        await switchTo(action.target);
                      });
                    }}
                  >
                    保存并切换
                  </Button>
                </>
              ) : (
                <Button
                  type="submit"
                  disabled={busy}
                  variant={
                    action?.type === "delete" ? "destructive" : "default"
                  }
                >
                  {busy
                    ? "正在处理…"
                    : action?.type === "delete"
                      ? "确认删除"
                      : "保存"}
                </Button>
              )}
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
