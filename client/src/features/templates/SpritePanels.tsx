/** 模板编辑中的 Remotion 资产：左侧从发布目录添加固定片段，右侧列出已添加片段；时间在预览下方时间轴拖动调整。 */
import { useState } from "react";
import { Plus, RefreshCw, Trash2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Environment } from "./api";
import type { useSpriteBindings } from "./useSpriteBindings";
import { newPlacement } from "@/features/sprites/model";

type Sprites = ReturnType<typeof useSpriteBindings>;

/** 左侧资产区：展开时读取目录；本地模板不支持绑定，只说明原因。 */
export function SpriteCatalog({ environment, sprites }: { environment: Environment; sprites: Sprites }) {
  const [open, setOpen] = useState(false);
  const usable = environment === "cloud" && sprites.status === "ready";
  return (
    <section aria-label="Remotion 资产" className="space-y-2 border-t p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold"><Wand2 className="size-3.5" aria-hidden="true" />Remotion 资产</h2>
        <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]" aria-expanded={open} onClick={() => { const next = !open; setOpen(next); if (next) sprites.refreshCatalog(); }}>{open ? "收起" : "展开"}</Button>
      </div>
      {environment !== "cloud" && <p role="status" className="text-[11px] text-muted-foreground">Remotion 资产只能添加到云端模板。</p>}
      {open && environment === "cloud" && <>
        <p className="text-[11px] text-muted-foreground">资产画面以透明层叠加在预览上，并随时间轴位置显示。</p>
        {sprites.catalogError && <p role="alert" className="text-[11px] text-destructive">{sprites.catalogError}</p>}
        {sprites.assets && !sprites.assets.length && <p role="status" className="text-[11px] text-muted-foreground">还没有资产，请在字效工作区的成功版本上选择「保存到资产」。</p>}
        <ul className="space-y-1">
          {sprites.assets?.map((asset) => (
            <li key={asset.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
              <span className="min-w-0"><span className="block truncate text-[11px] font-medium">{asset.name}</span><span className="block text-[10px] tabular-nums text-muted-foreground">{Number(asset.seconds.toFixed(1))} 秒</span></span>
              <Button type="button" variant="outline" size="sm" className="h-6 gap-1 px-2 text-[11px]" disabled={!usable} aria-label={`添加 Remotion 资产：${asset.name}`} onClick={() => sprites.setPlacements((items) => [...items, newPlacement(asset)])}><Plus className="size-3" aria-hidden="true" />添加</Button>
            </li>
          ))}
        </ul>
        <Button type="button" variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[11px]" onClick={sprites.refreshCatalog}><RefreshCw className="size-3" aria-hidden="true" />刷新目录</Button>
      </>}
    </section>
  );
}

/** 右侧已添加列表：只显示名称和时间，可移除；读取失败时提供重试，保存前不覆盖服务端绑定。 */
export function SpritePlacements({ sprites, duration }: { sprites: Sprites; duration: number }) {
  if (sprites.status === "unavailable") return null;
  const { placements, assets } = sprites;
  return (
    <section aria-label="已添加 Remotion 资产" className="space-y-2 border-b p-4">
      <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">Remotion 资产</h2><span className="text-[11px] tabular-nums text-muted-foreground">{placements.length}</span></div>
      {sprites.status === "loading" && <p role="status" className="text-[11px] text-muted-foreground">正在读取已添加资产…</p>}
      {sprites.status === "error" && <div role="alert" className="space-y-1 text-[11px] text-destructive"><p>{sprites.message}</p><Button type="button" variant="outline" size="sm" className="h-6 px-2 text-[11px]" onClick={sprites.retry}>重试读取</Button></div>}
      {sprites.status === "ready" && !placements.length && <p className="text-[11px] text-muted-foreground">从左侧 Remotion 资产添加，在时间轴上拖动调整位置。</p>}
      {sprites.status === "ready" && sprites.catalogError && <p role="alert" className="text-[11px] text-destructive">{sprites.catalogError}</p>}
      {sprites.status === "ready" && <ul className="space-y-1">
        {placements.map((placement) => {
          const asset = assets?.find((item) => item.id === placement.spriteId);
          const name = asset?.name ?? (assets ? "未找到的资产" : "读取中…");
          const end = Math.min(duration, placement.start + placement.duration);
          return <li key={placement.id} aria-label={`Remotion 资产：${name}`} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
            <span className="min-w-0"><span className="block truncate text-[12px] font-medium">{name}</span><span className="block text-[11px] tabular-nums text-muted-foreground">{Number(placement.start.toFixed(1))}～{Number(end.toFixed(1))} 秒{assets && !asset && " · 资产已不可用，保存前请移除"}</span></span>
            <Button type="button" variant="ghost" size="icon" className="size-6" aria-label={`移除 Remotion 资产：${name}`} onClick={() => sprites.setPlacements((items) => items.filter((item) => item.id !== placement.id))}><Trash2 className="size-3.5" aria-hidden="true" /></Button>
          </li>;
        })}
      </ul>}
    </section>
  );
}
