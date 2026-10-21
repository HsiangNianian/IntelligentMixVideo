/**
 * 新建云端模板对话框：填写名称（必填，最多 100 字）与说明（选填，最多 1000 字）后进入编辑器。
 * 这里只创建草稿，不写入存储；在编辑器里点击保存才会真正保存，脚注向用户说明这一点。
 */
import { useEffect, useState, type FormEvent } from "react";
import { Icon } from "../icons";
import { Button, TextField } from "../ui";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "../dialog";

interface NewTemplateDialogProps {
  /** 是否显示对话框；每次打开都清空上次的输入。 */
  open: boolean;
  existingNames: string[];
  onClose: () => void;
  onCreate: (draft: { name: string; description: string }) => void;
}

/** 每次打开都清空上次的输入；提交时去掉首尾空白，名称为空则提示而不关闭。 */
export function NewTemplateDialog({ open, existingNames, onClose, onCreate }: NewTemplateDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setTouched(false);
  }, [open]);
  const duplicate = existingNames.includes(name.trim());
  const nameError = touched ? !name.trim() ? "请输入模板名称" : duplicate ? "模板名称已存在，请使用其他名称" : undefined : undefined;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!open || !name.trim() || duplicate) return;
    onCreate({ name: name.trim(), description: description.trim() });
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <form onSubmit={submit} noValidate>
          <DialogHeader
            tone="brand"
            icon={<Icon name="cloud" variant="duotone" />}
            title="新建云端模板"
            description="云端模板团队共享，保存后所有人都能看到和使用。"
          />
          <div className="space-y-5 px-6 pb-4 pt-4">
            <TextField label="模板名称" required autoFocus value={name} onChange={setName} maxLength={100} placeholder="例如：带货口播 · 黄色花字" error={nameError} />
            <TextField label="模板描述" multiline value={description} onChange={setDescription} maxLength={1000} placeholder="这个模板适合什么样的视频？" />
          </div>
          <DialogFooter
            note={
              <span className="flex items-center gap-1.5">
                <Icon name="info-circle" className="size-3.5 shrink-0" />
                进入编辑器后点「保存」才会写入
              </span>
            }
          >
            <Button variant="ghost" onClick={onClose}>取消</Button>
            <Button type="submit" variant="primary">
              进入编辑 <Icon name="arrow-right" />
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
