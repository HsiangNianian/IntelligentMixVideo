/** 模板编辑中的 AI 资产：左侧按分类从发布目录添加或删除资产，右侧列出模板里的片段、所占的模板对象，并可选中调整参数。 */
import { useId, useState } from "react";
import { toast } from "sonner";
import { Pencil, RefreshCw, Trash2, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Hint } from "@/components/Hint";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { deleteSprite, renameSprite } from "@/features/sprites/api";
import { cn } from "@/lib/utils";
import type { SpriteAsset } from "@/features/sprites/model";
import { CATEGORIES, categoryOf, type CategoryId } from "@/features/sprites/parameters";
import type { Environment } from "./api";
import { textRoles, type RemotionClip } from "./model";
import { clipSlot } from "./clips";
import { effectTargets } from "./effects";
import type { useSpriteCatalog } from "./useSpriteCatalog";
import { AssetBrowser } from "./AssetBrowser";
import { SpriteCover } from "./SpriteCover";

type Catalog = ReturnType<typeof useSpriteCatalog>;

/**
 * 左侧资产区：由素材来源页签触发目录读取，按字效、滤镜、转场与视频特效分类，用与特效资产相同的搜索与封面卡片浏览，可重命名或确认后删除共享资产；本地模板不支持 AI 资产，只说明原因。
 * `placed` 是当前草稿（含未保存的修改）摆放的资产：它们不能删除，否则之后保存模板会因资产未发布被拒绝；重命名不受影响，
 * 模板只保存资产 ID，名称都从目录读取，改名后刷新目录即可。
 */
export function SpriteCatalog({ environment, catalog, placed, onAdd }: { environment: Environment; catalog: Catalog; placed: ReadonlySet<string>; onAdd: (asset: SpriteAsset) => void }) {
  const [deleting, setDeleting] = useState<SpriteAsset | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const [renaming, setRenaming] = useState<SpriteAsset | null>(null);
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);
  const [renameError, setRenameError] = useState("");
  const renameId = useId();
  // 未手动选择时打开第一个有资产的分类，新保存的资产不会藏在空分类后面。
  const [chosen, setChosen] = useState<CategoryId | null>(null);
  const usable = environment === "cloud";
  const category = chosen ?? CATEGORIES.find((item) => catalog.assets?.some((asset) => categoryOf(asset.kind) === item.id))?.id ?? "text";
  const shown = catalog.assets?.filter((asset) => categoryOf(asset.kind) === category);

  /** 删除确认的资产；仍被模板使用等失败原因留在弹窗里，成功后刷新目录。 */
  async function remove() {
    if (!deleting || removing) return;
    setRemoving(true); setRemoveError("");
    try {
      await deleteSprite(deleting.id);
      toast(`已删除资产「${deleting.name}」`);
      setDeleting(null); catalog.refresh();
    } catch (reason) { setRemoveError(reason instanceof Error ? reason.message : "删除资产失败"); }
    finally { setRemoving(false); }
  }

  /** 提交新名称；失败原因（如 422）留在弹窗里允许修改后重试，成功后刷新目录，卡片、片段列表与时间轴标签随之更新。 */
  async function rename() {
    if (!renaming || saving) return;
    const trimmed = newName.trim();
    if (!trimmed) { setRenameError("请输入资产名称"); return; }
    setSaving(true); setRenameError("");
    try {
      const asset = await renameSprite(renaming.id, trimmed);
      toast(`已重命名为「${asset.name}」`);
      setRenaming(null); catalog.refresh();
    } catch (reason) { setRenameError(reason instanceof Error ? reason.message : "重命名失败"); }
    finally { setSaving(false); }
  }

  return (
    <section aria-label="AI 资产" className="space-y-2 border-t p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold"><Wand2 className="size-3.5" aria-hidden="true" />AI 资产</h2>
      </div>
      {environment !== "cloud" && <p role="status" className="text-[11px] text-muted-foreground">AI 资产只能添加到云端模板。</p>}
      {environment === "cloud" && <>
        <p className="text-[11px] text-muted-foreground">资产画面以透明层叠加在预览上。字效作用于当前选择的标题或字幕，转场加入统一列表，视频特效与画面特效共用一个名额。</p>
        {catalog.error && <p role="alert" className="text-[11px] text-destructive">{catalog.error}</p>}
        {catalog.assets && !catalog.assets.length && <p role="status" className="text-[11px] text-muted-foreground">还没有资产，请在字效工作区的成功版本上选择「保存到资产」。</p>}
        {!!catalog.assets?.length && shown && (
          <AssetBrowser
            categories={CATEGORIES.map((item) => ({ value: item.id, label: item.label, count: catalog.assets!.filter((asset) => categoryOf(asset.kind) === item.id).length }))}
            category={category}
            onCategory={(value) => setChosen(value as CategoryId)}
            categoriesLabel="AI 资产分类"
            searchLabel="搜索 AI 资产"
            searchPlaceholder="名称"
            noun="资产"
            items={shown}
            searchText={(asset) => asset.name}
            card={(asset) => {
              const inUse = placed.has(asset.id);
              return {
                id: asset.id,
                name: asset.name,
                label: `添加 AI 资产：${asset.name}`,
                cover: (active) => <SpriteCover asset={asset} active={active} />,
                coverClassName: "h-auto",
                disabled: !usable,
                onSelect: () => onAdd(asset),
                // 禁用的按钮不触发悬停提示，原因以卡片下方文字说明。
                actions: <>
                  <Hint label="重命名资产"><Button type="button" variant="secondary" size="icon" className="size-6" aria-label={`重命名 AI 资产：${asset.name}`} onClick={() => { setRenaming(asset); setNewName(asset.name); setRenameError(""); }}><Pencil className="size-3.5" aria-hidden="true" /></Button></Hint>
                  <Hint label="删除资产" disabled={inUse}><Button type="button" variant="secondary" size="icon" className="size-6" disabled={inUse} aria-describedby={inUse ? `sprite-in-use-${asset.id}` : undefined} aria-label={`删除 AI 资产：${asset.name}`} onClick={() => { setDeleting(asset); setRemoveError(""); }}><Trash2 className="size-3.5" aria-hidden="true" /></Button></Hint>
                </>,
                // 画布规格必须一致才能叠加，所以每张卡片都标出预览长度与画布。
                note: <span className="block px-1 pt-1 text-[10px] tabular-nums text-muted-foreground">预览 {Number(asset.seconds.toFixed(1))} 秒 · {asset.width}×{asset.height} @ {asset.fps} FPS{inUse && <span id={`sprite-in-use-${asset.id}`} className="block">当前模板正在使用，移除片段后才能删除</span>}</span>,
              };
            }}
          />
        )}
        <Button type="button" variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[11px]" onClick={catalog.refresh}><RefreshCw className="size-3" aria-hidden="true" />刷新目录</Button>
      </>}
      <Dialog open={renaming !== null} onOpenChange={(next) => { if (!next && !saving) setRenaming(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>重命名 AI 资产</DialogTitle><DialogDescription>只修改资产库里显示的名称，所有人可见；代码、参数和已使用它的模板不受影响，名称不要求唯一。</DialogDescription></DialogHeader>
          <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void rename(); }}>
            <div className="space-y-2">
              <Label htmlFor={`${renameId}-name`}>资产名称</Label>
              <Input id={`${renameId}-name`} required maxLength={100} value={newName} disabled={saving} onChange={(event) => { setNewName(event.target.value); setRenameError(""); }} />
            </div>
            {renameError && <p role="alert" className="text-sm text-destructive">{renameError}</p>}
            <DialogFooter><Button type="button" variant="outline" disabled={saving} onClick={() => setRenaming(null)}>取消</Button><Button type="submit" disabled={saving}>{saving ? "正在保存…" : "保存名称"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={deleting !== null} onOpenChange={(next) => { if (!next && !removing) setDeleting(null); }}>
        <DialogContent><DialogHeader><DialogTitle>删除 AI 资产</DialogTitle><DialogDescription>将从共享资产库永久删除「{deleting?.name}」，所有人都无法再添加它。仍被云端模板使用的资产不能删除；生成它的字效会话不受影响，可以重新保存到资产。</DialogDescription></DialogHeader>
          {removeError && <p role="alert" className="text-sm text-destructive">{removeError}</p>}
          <DialogFooter><Button type="button" variant="outline" disabled={removing} onClick={() => setDeleting(null)}>取消</Button><Button type="button" variant="destructive" disabled={removing} onClick={() => void remove()}>{removing ? "正在删除…" : "确认删除"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** 右侧片段列表：显示名称、时间与是否改过参数，点选打开参数设置，可移除；本地模板不显示。资产已不可用时提示先移除，否则服务端会拒绝保存。 */
export function SpriteClips({ environment, clips, catalog, duration, hidden, selected, onSelect, onRemove }: {
  environment: Environment; clips: RemotionClip[]; catalog: Catalog; duration: number | undefined;
  /** 所作用文字在当前预览中不显示的字效，与文字一起标为未应用。 */
  hidden?: ReadonlySet<string>;
  selected: string | null; onSelect: (id: string) => void; onRemove: (id: string) => void;
}) {
  if (environment !== "cloud") return null;
  const { assets } = catalog;
  return (
    <section aria-label="已添加 AI 资产" className="space-y-2 border-b p-4">
      <div className="flex items-center justify-between"><h2 className="text-[13px] font-semibold">AI 资产</h2><span className="text-[11px] tabular-nums text-muted-foreground">{clips.length}</span></div>
      {!clips.length && <p className="text-[11px] text-muted-foreground">从左侧 AI 资产添加，点选后在右侧调整时间、参数与位置。</p>}
      {catalog.error && <p role="alert" className="text-[11px] text-destructive">{catalog.error}</p>}
      <ul className="space-y-1">
        {clips.map((clip) => {
          const asset = assets?.find((item) => item.id === clip.sprite_id);
          const name = asset?.name ?? (assets ? "未找到的资产" : "读取中…");
          const end = duration === undefined ? undefined : Math.min(duration, clip.start + (clip.duration ?? asset?.seconds ?? 0));
          const timing = end === undefined ? "等待预览时间" : end <= clip.start || hidden?.has(clip.id) ? "当前视频未应用" : `${Number(clip.start.toFixed(1))}～${Number(end.toFixed(1))} 秒`;
          const edited = Object.keys(clip.parameters).length + Object.keys(clip.transform).length > 0;
          const slot = clipSlot(clip, asset);
          const place = slot === "title" || slot === "subtitle" ? `${clip.language === "en" ? slot === "title" ? "英文标题" : "英文字幕" : textRoles[slot]}字效` : slot === "transition" ? `镜头转场 ${clip.transition_boundary ?? ""}`.trim() : slot ? effectTargets[slot] : asset ? "未选择作用对象" : "";
          return <li key={clip.id} aria-label={`AI 资产：${name}`} className={cn("flex items-center justify-between gap-2 rounded-md border px-2 py-1.5", selected === clip.id && "border-primary ring-1 ring-primary/20")}>
            <button type="button" className="min-w-0 flex-1 text-left" aria-pressed={selected === clip.id} aria-label={`设置 AI 资产：${name}`} onClick={() => onSelect(clip.id)}><span className="block truncate text-[12px] font-medium">{name}</span><span className="block text-[11px] tabular-nums text-muted-foreground">{place && `${place} · `}{timing}{edited && " · 已调整参数"}{assets && !asset && " · 资产已不可用，保存前请移除"}</span></button>
            <Button type="button" variant="ghost" size="icon" className="size-6" aria-label={`移除 AI 资产：${name}`} onClick={() => onRemove(clip.id)}><Trash2 className="size-3.5" aria-hidden="true" /></Button>
          </li>;
        })}
      </ul>
    </section>
  );
}
