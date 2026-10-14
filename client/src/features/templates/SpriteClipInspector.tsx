/**
 * 模板里一次 Remotion 摆放的设置：资产对外的参数（可逐项恢复默认值）、宿主变换，以及所占的模板对象或起点；
 * 字效选择作用的标题或字幕，转场选择视频交界处，两者的时间跟随所占对象；滤镜与视频特效设置起点。修改只进入草稿，随模板保存时服务端再按发布契约检查。
 */
import { useState } from "react";
import { Bold, Italic, RotateCcw, Strikethrough, Trash2, Underline, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Hint } from "@/components/Hint";
import { ParameterControl, type ParameterValue } from "@/components/ParameterControl";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { CATEGORIES, categoryOf, transformField, transformFields, type TransformField } from "@/features/sprites/parameters";
import { textRoles, type RemotionClip } from "./model";
import { BOUND_TEXT_PARAMETERS, type bindClip } from "./clips";

/** 关键词相关参数：键名以 keyword 开头（keywords、keywordColor、keywordBold……）。 */
const isKeywordParameter = (key: string) => /^keyword/i.test(key);
/** 字效必备的关键词样式参数，与服务端 `categories.TEXT_KEYWORDS` 一致；缺任何一个即为早于该契约发布的字效。 */
const KEYWORD_STYLE_KEYS = ["keywordBold", "keywordItalic", "keywordUnderline", "keywordStrikeout", "keywordColor", "keywordSize"];
/** 关键词颜色（空串沿用文字颜色）与字号（0 沿用文字字号）的开关打开时的初值与字号下限，与 IMV 模板一致。 */
const KEYWORD_COLOR_ON = "#FFFF00";
const KEYWORD_SIZE_MIN = 12;
/** 关键词局部样式开关：这四个布尔参数合并为一组切换按钮。 */
const KEYWORD_STYLES = [
  ["keywordBold", "加粗", Bold],
  ["keywordItalic", "斜体", Italic],
  ["keywordUnderline", "下划线", Underline],
  ["keywordStrikeout", "删除线", Strikethrough],
] as const;

/** 选中片段的右栏；资产已不在目录中时只允许移除。调用方按片段 ID 设 key，切换片段时页签回到「参数与变换」。 */
export function SpriteClipInspector({ clip, asset, duration, onParameter, onTransform, onStart, onBind, onRemove, onClose }: {
  clip: RemotionClip;
  asset: SpriteAsset | undefined;
  duration: number | undefined;
  onParameter: (key: string, value: ParameterValue | undefined) => void;
  onTransform: (patch: Partial<Record<TransformField, number | undefined>>) => void;
  onStart: (start: number) => void;
  onBind: (binding: Parameters<typeof bindClip>[2]) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"appearance" | "keyword">("appearance");
  const isText = asset?.kind === SpriteKind.TEXT;
  const isTransition = asset?.kind === SpriteKind.TRANSITION_OVERLAY;
  const legacyTextTarget = clip.business_target === "title" || clip.business_target === "subtitle" ? clip.business_target : undefined;
  // 作用于文字时，文字与关键词由该文字对象提供，不在这里单独设置。
  const shown = (key: string) => !(clip.text_target || legacyTextTarget) || !BOUND_TEXT_PARAMETERS.includes(key);
  // 只有字效把关键词参数移到单独页签；其他类型里以 keyword 开头的参数仍留在参数区。
  const keywordParameters = asset && isText ? asset.parameters.filter(({ field }) => isKeywordParameter(field.key) && shown(field.key)) : [];
  const styleSwitches = keywordParameters.flatMap((item) => {
    const style = KEYWORD_STYLES.find(([key]) => key === item.field.key);
    return item.field.kind === "boolean" && style ? [{ ...item, label: style[1], Icon: style[2] }] : [];
  });
  const colorItem = keywordParameters.find(({ field }) => field.key === "keywordColor" && field.kind === "string");
  const sizeItem = keywordParameters.find(({ field }) => field.key === "keywordSize" && field.kind === "integer");
  const legacyText = isText && KEYWORD_STYLE_KEYS.some((key) => !keywordParameters.some(({ field }) => field.key === key));
  const fontSize = asset?.parameters.find(({ field }) => field.key === "fontSize");
  const appearanceParameters = asset?.parameters.filter((item) => !keywordParameters.includes(item) && shown(item.field.key)) ?? [];
  const activeTab = isText ? tab : "appearance";
  /** 参数当前值：本次摆放的覆盖优先，否则取发布默认值。 */
  const valueOf = ({ field, defaultValue }: NonNullable<typeof asset>["parameters"][number]) => clip.parameters[field.key] ?? defaultValue;
  /** 写入参数覆盖；与发布默认值相同时清除覆盖。 */
  const setValue = ({ field, defaultValue }: NonNullable<typeof asset>["parameters"][number], value: ParameterValue) =>
    onParameter(field.key, JSON.stringify(value) === JSON.stringify(defaultValue) ? undefined : value);
  /** 单个参数控件，已改动时附「恢复默认」。 */
  const parameterRow = ({ field, editable, defaultValue }: NonNullable<typeof asset>["parameters"][number]) => {
    const changed = Object.prototype.hasOwnProperty.call(clip.parameters, field.key);
    return <div key={field.key} className="flex items-end gap-1.5">
      <ParameterControl className="min-w-0 flex-1" field={field} value={changed ? clip.parameters[field.key]! : defaultValue} disabled={!editable}
        onChange={(value) => onParameter(field.key, JSON.stringify(value) === JSON.stringify(defaultValue) ? undefined : value)} />
      {changed && <Hint label={`恢复默认：${field.label}`}><Button type="button" variant="ghost" size="icon" className="mb-0.5 size-8 shrink-0" aria-label={`恢复默认：${field.label}`} onClick={() => onParameter(field.key, undefined)}><RotateCcw className="size-3.5" aria-hidden="true" /></Button></Hint>}
    </div>;
  };
  const category = asset ? CATEGORIES.find((item) => item.id === categoryOf(asset.kind))?.label : undefined;
  return <section aria-label="Remotion 资产设置" className="min-w-0">
    <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3">
      <div className="min-w-0"><h2 className="truncate text-[13px] font-semibold leading-5">{asset?.name ?? "未找到的资产"}</h2><p className="mt-0.5 text-[11px] leading-3 text-muted-foreground">{category ?? "资产已不可用，保存前请移除"}</p></div>
      <Hint label="关闭资产设置"><Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label="关闭资产设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    {asset && isText && <div role="tablist" aria-label="资产设置分类" className="grid grid-cols-2 border-b px-4">
      {([["appearance", "参数与变换"], ["keyword", "关键词设置"]] as const).map(([value, label]) =>
        <button key={value} type="button" role="tab" aria-selected={activeTab === value} onClick={() => setTab(value)} className="border-b-2 border-transparent px-1 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring data-[selected=true]:border-primary data-[selected=true]:text-primary" data-selected={activeTab === value}>{label}</button>)}
    </div>}
    {asset && isText && <fieldset hidden={activeTab !== "keyword"} className="space-y-3 px-4 py-3">
      <legend className="sr-only">关键词设置</legend>
      {legacyText && <p className="text-[11px] text-muted-foreground">这个字效发布于关键词设置之前，缺少关键词样式参数。请在字效工作区让 Agent 改出新版本并重新保存到资产。</p>}
      {styleSwitches.length > 0 && <div className="space-y-2">
        <h3 className="text-xs font-medium">关键词样式</h3>
        {/* 四种局部样式可组合，按下即开启对应参数。 */}
        <ToggleGroup type="multiple" variant="outline" size="sm" className="w-full"
          value={styleSwitches.filter((item) => valueOf(item) === true).map(({ field }) => field.key)}
          onValueChange={(on) => styleSwitches.forEach((item) => {
            const value = on.includes(item.field.key);
            if (item.editable && value !== (valueOf(item) === true)) setValue(item, value);
          })}>
          {styleSwitches.map(({ field, editable, label, Icon }) => <ToggleGroupItem key={field.key} value={field.key} aria-label={label} disabled={!editable} className="flex-1"><Icon aria-hidden="true" /></ToggleGroupItem>)}
        </ToggleGroup>
      </div>}
      {colorItem && <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="keyword-color-on" className="text-xs">设置关键词颜色</Label>
          <Switch id="keyword-color-on" checked={valueOf(colorItem) !== ""} disabled={!colorItem.editable} onCheckedChange={(on) => setValue(colorItem, on ? KEYWORD_COLOR_ON : "")} />
        </div>
        <div className="flex items-center gap-3">
          <input type="color" aria-label={colorItem.field.label} value={String(valueOf(colorItem) || KEYWORD_COLOR_ON)} disabled={!colorItem.editable || valueOf(colorItem) === ""}
            onChange={(event) => setValue(colorItem, event.target.value.toUpperCase())} className="h-9 w-14 rounded border bg-transparent p-1 disabled:cursor-not-allowed" />
          <span className="text-xs text-muted-foreground">{String(valueOf(colorItem)) || "保持文字原色"}</span>
        </div>
      </div>}
      {sizeItem && <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="keyword-size-on" className="text-xs">设置关键词字号</Label>
          <Switch id="keyword-size-on" checked={valueOf(sizeItem) !== 0} disabled={!sizeItem.editable}
            onCheckedChange={(on) => setValue(sizeItem, on ? Math.min(sizeItem.field.maximum ?? 300, Math.max(KEYWORD_SIZE_MIN, Math.round(Number(fontSize ? valueOf(fontSize) : 40)))) : 0)} />
        </div>
        <ParameterControl field={{ ...sizeItem.field, minimum: KEYWORD_SIZE_MIN }} value={valueOf(sizeItem) || KEYWORD_SIZE_MIN} disabled={!sizeItem.editable || valueOf(sizeItem) === 0}
          onChange={(value) => setValue(sizeItem, value)} />
      </div>}
      {keywordParameters.filter((item) => item !== colorItem && item !== sizeItem && !styleSwitches.some(({ field }) => field.key === item.field.key)).map(parameterRow)}
    </fieldset>}
    {asset && <>
      <fieldset hidden={activeTab !== "appearance"} className="space-y-3 border-t px-4 py-3">
        <legend className="sr-only">资产参数</legend>
        <h3 className="text-xs font-medium">参数</h3>
        {!appearanceParameters.length && <p className="text-[11px] text-muted-foreground">这个资产没有可调整的参数。</p>}
        {appearanceParameters.map(parameterRow)}
      </fieldset>
      <fieldset hidden={activeTab !== "appearance"} className="space-y-3 border-t px-4 py-3">
        <legend className="sr-only">位置与变换</legend>
        <div className="flex items-center justify-between"><h3 className="text-xs font-medium">{transformFields(asset.kind).length === 1 ? "强度" : "位置与变换"}</h3>
          {Object.keys(clip.transform).length > 0 && <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={() => onTransform({ x: undefined, y: undefined, scale: undefined, rotation: undefined, opacity: undefined })}>恢复默认</Button>}
        </div>
        {transformFields(asset.kind).map((name) => {
          const { fallback, ...field } = transformField(name, asset.kind, asset.width, asset.height);
          return <ParameterControl key={name} field={field} value={clip.transform[name] ?? fallback(asset.box)} disabled={false}
            onChange={(value) => onTransform({ [name]: value as number })} />;
        })}
        {transformFields(asset.kind).length > 1 && <p className="text-[11px] text-muted-foreground">也可以在预览画面上拖动所选资产调整位置。</p>}
      </fieldset>
    </>}
    <div className="space-y-3 border-t px-4 py-3">
      {isText ? <div className="space-y-2">
        <h3 className="text-xs font-medium">作用对象</h3>
        <ToggleGroup type="single" variant="outline" size="sm" className="w-full" aria-label="字效作用对象" value={clip.text_target ?? ""}
          onValueChange={(value) => { if (value === "title" || value === "subtitle") onBind({ text_target: value }); }}>
          {(["title", "subtitle"] as const).map((role) => <ToggleGroupItem key={role} value={role} className="flex-1 text-xs">{textRoles[role]}</ToggleGroupItem>)}
        </ToggleGroup>
        <p className="text-[11px] text-muted-foreground">{clip.text_target ? `由这个字效显示${textRoles[clip.text_target]}：文字与关键词取自${textRoles[clip.text_target]}，时间跟随它。` : legacyTextTarget ? `当前与 IMS ${textRoles[legacyTextTarget]}同时显示。重新选择作用对象后，将改为仅显示 Remotion 字效。` : "请选择作用的标题或底部字幕，否则无法保存模板。"}</p>
      </div> : isTransition ? <div className="space-y-2">
        <h3 className="text-xs font-medium">转场 {clip.transition_boundary}</h3>
        <p className="text-[11px] text-muted-foreground">持续时间与选择方式在「镜头转场」中统一设置。</p>
      </div> : <ParameterControl field={{ key: "start", label: "开始时间（秒）", kind: "number", minimum: 0, maximum: duration }} value={clip.start} disabled={false}
        onChange={(value) => onStart(value as number)} />}
      <Button type="button" variant="outline" className="w-full gap-1.5" onClick={onRemove}><Trash2 className="size-3.5" aria-hidden="true" />移除这个资产</Button>
    </div>
  </section>;
}
