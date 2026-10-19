/**
 * 新建模板对话框：填写名称（必填，最多 100 字）与说明（选填，最多 1000 字）后进入编辑器。
 * 这里只创建草稿，不写入存储；在编辑器里点击保存才会真正保存，脚注向用户说明这一点。
 */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Icon } from "../icons";
import { Button, TextField } from "../ui";
import { Dialog, DialogContent, DialogFooter, DialogHeader } from "../dialog";
import type { Library } from "../mock";

interface NewTemplateDialogProps {
  /** 要新建的模板库；null 表示对话框关闭。 */
  library: Library | null;
  onClose: () => void;
  onCreate: (draft: { library: Library; name: string; description: string }) => void;
}

/** 每次打开都清空上次的输入；提交时去掉首尾空白，名称为空则提示而不关闭。 */
export function NewTemplateDialog({ library, onClose, onCreate }: NewTemplateDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!library) return;
    setName("");
    setDescription("");
    setTouched(false);
  }, [library]);
  // 关闭动画期间 library 已为 null，标题沿用上一次的库，避免文案在淡出时跳变。
  const last = useRef(library);
  if (library) last.current = library;
  const nameError = touched && !name.trim() ? "请填写模板名称" : undefined;
  const cloud = (library ?? last.current) === "cloud";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!library || !name.trim()) return;
    onCreate({ library, name: name.trim(), description: description.trim() });
  };

  return (
    <Dialog open={library !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} noValidate>
          <DialogHeader
            tone="accent"
            icon={<Icon name={cloud ? "cloud" : "laptop-minimalistic"} variant="duotone" />}
            title={cloud ? "新建云端模板" : "新建本地模板"}
            description={cloud ? "云端模板团队共享，保存后所有人都能看到和使用。" : "本地模板只保存在这台电脑上，不需要连接服务。"}
          />
          <div className="space-y-5 px-6 pb-4 pt-4">
            <TextField label="模板名称" required autoFocus value={name} onChange={setName} maxLength={100} placeholder="例如：带货口播 · 黄色花字" error={nameError} />
            <TextField label="说明" multiline value={description} onChange={setDescription} maxLength={1000} placeholder="这个模板适合什么样的视频？" />
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
              开始编辑 <Icon name="arrow-right" />
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
