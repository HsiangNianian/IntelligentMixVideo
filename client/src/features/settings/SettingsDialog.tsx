/** 设置对话框：普通客户端只保留通用设置，Debug 另展示模块；保存成功或取消时关闭，丢弃其他未保存草稿。 */
import { isTauri } from "@tauri-apps/api/core";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PluginSettings } from "./PluginSettings";

/** 居中宽弹窗承载模块设置；Radix 关闭即卸载内容，下次打开重新读取目录和已存值。 */
export function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-update-safe className="flex h-[min(600px,70dvh)] flex-col gap-0 overflow-hidden rounded-[20px] border-line-strong bg-surface p-0 shadow-pop sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b border-line bg-surface px-6 py-5 pr-12">
          <DialogTitle>设置</DialogTitle>
          <DialogDescription className="text-xs leading-5">
            {isTauri() ? "配置保存在当前客户端。" : "浏览器预览仅在当前页面保存配置，刷新后丢失。"}
            {import.meta.env.IMV_DEBUG === "true" && <>
              {isTauri() && "密钥以明文保存在本地文件中。"}
              Remotion Agent 与 IMS 使用当前客户端保存的凭据；Debug 另提供服务端模块设置。
            </>}
          </DialogDescription>
        </DialogHeader>
        <PluginSettings onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}
