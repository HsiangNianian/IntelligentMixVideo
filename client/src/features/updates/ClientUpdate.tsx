/** 设置中的版本检查与下载状态；重启提示在根页面挂载，不随设置关闭而消失。 */
import { toast } from 'sonner';
import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { APP_VERSION } from '@/lib/version';
import { cancelUpdate, checkUpdate, downloadUpdate, updaterReady, useClientUpdate } from './store';
/** 设置弹窗中的更新区块：挂载即检查，展示版本、状态与进度，提供下载、取消与 deb 手动下载链接。 */
export function ClientUpdate() {
  const state = useClientUpdate();
  useEffect(() => { void checkUpdate(); }, []);
  return <section aria-label="客户端更新" className="space-y-3 border-t pt-5 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">客户端更新</h3><span>界面 v{APP_VERSION} · 原生 v{state.nativeVersion}</span></div>
    <p role="status" className="text-xs text-muted-foreground">{state.message}{state.progress !== undefined && ` ${state.progress}%`}</p>
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" size="sm" disabled={['disabled', 'checking', 'downloading', 'waiting', 'applying'].includes(state.phase)} onClick={() => void checkUpdate()}>检查更新</Button>
      {state.candidate && ['available', 'error', 'manual'].includes(state.phase) && <Button type="button" size="sm" onClick={() => void downloadUpdate()}>{state.candidate.mode === 'manual' ? '获取安装包' : '一键更新'}</Button>}
      {state.phase === 'waiting' && <Button type="button" variant="outline" size="sm" onClick={cancelUpdate}>取消此次更新</Button>}
    </div>
    {state.links?.map((url, index) => <p key={url}><a href={url} target="_blank" rel="noreferrer" className="break-all text-xs underline">下载源 {index + 1}：{new URL(url).hostname}</a><Button type="button" variant="ghost" size="sm" onClick={() => void navigator.clipboard.writeText(url).then(() => toast.success('链接已复制')).catch(() => toast.error('复制失败，请手动复制下载链接'))}>复制链接</Button></p>)}
    {state.links && state.sha256 && <p className="break-all text-xs text-muted-foreground">下载后先用 sha256sum 核对以下摘要，再安装：<code className="block select-all">{state.sha256}</code></p>}
    {state.candidate && <p className="text-xs text-muted-foreground">{state.candidate.mode === 'hot' ? '兼容当前客户端，更新时窗口保持打开。' : state.candidate.mode === 'native' ? '此版本需要完整升级，准备完成后会提示并自动重启。' : 'deb 安装包需要手动安装；已保存内容会保留。'}</p>}
  </section>;
}
/** 根页面常驻部分：挂载后确认当前界面可用，并在设置关闭后继续显示等待浮层与应用中的状态对话框。 */
export function UpdateLifecycle() {
  const state = useClientUpdate();
  useEffect(() => { void updaterReady(); }, []);
  return <>
    {state.phase === 'waiting' && <aside className="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg border bg-background p-4 shadow-lg" aria-label="等待客户端更新"><p className="mb-2 text-sm" role="status">{state.message}</p><Button size="sm" variant="outline" onClick={cancelUpdate}>取消此次更新</Button></aside>}
    <Dialog open={state.phase === 'applying'}>
      <DialogContent data-update-safe showCloseButton={false} onEscapeKeyDown={e => e.preventDefault()} onPointerDownOutside={e => e.preventDefault()}>
        <DialogHeader><DialogTitle>{state.candidate?.mode === 'hot' ? '正在应用界面更新' : '正在重新启动客户端'}</DialogTitle><DialogDescription>已保存当前工作位置，请稍候…</DialogDescription></DialogHeader>
      </DialogContent>
    </Dialog>
  </>;
}
