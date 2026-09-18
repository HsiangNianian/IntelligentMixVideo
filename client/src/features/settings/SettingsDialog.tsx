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
            {import.meta.env.IMV_DEBUG === "true" ? <>
              {isTauri() ? "配置仅保存到当前客户端，API Key 随配置以明文保存在本地文件中。" : "浏览器预览仅在当前页面保存配置，刷新后丢失。"}
              已接入的模块在请求时使用保存的配置，不修改服务端配置文件。
            </> : "当前仅展示客户端设置，服务端配置由服务端环境管理。"}
          </DialogDescription>
        </DialogHeader>
        <PluginSettings />
      </DialogContent>
    </Dialog>
  );
}
