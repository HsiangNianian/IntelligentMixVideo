/** 设置对话框：左侧模块纵向导航、右侧滚动内容；打开时读取目录，关闭丢弃未保存草稿。 */
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
      <DialogContent className="flex h-[min(600px,70dvh)] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="shrink-0 border-b px-6 py-5 pr-12">
          <DialogTitle>设置</DialogTitle>
          <DialogDescription className="text-xs leading-5">
            {isTauri() ? "配置保存在当前客户端，密钥以明文保存在本地文件中。" : "浏览器预览仅在当前页面保存配置，刷新后丢失。"}
            Remotion Agent 与 IMS 使用当前客户端保存的凭据；Debug 另提供服务端模块设置。
          </DialogDescription>
        </DialogHeader>
        <PluginSettings />
      </DialogContent>
    </Dialog>
  );
}
