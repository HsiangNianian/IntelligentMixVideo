/** 画面对象参数面板共用的删除按钮，不依赖资产浏览或具体对象类型。 */
import { Button } from "@/components/ui/button";

/** 先更新草稿，再关闭设置；删除失败时保留当前面板。 */
export function RemoveObjectButton({ onRemove, onClose }: { onRemove: () => void; onClose: () => void }) {
  return <Button type="button" variant="outline" className="template-inspector-remove w-full" onClick={() => { onRemove(); onClose(); }}>移除当前画面对象</Button>;
}
