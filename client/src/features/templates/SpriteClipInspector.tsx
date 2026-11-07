/**
 * 模板里一次 Remotion 摆放的设置，只影响这次摆放、不改资产：字效调示例文字（多行字效逐行填写、双语字效中英各一行，
 * 用换行连接）、字号、字体与字重（双栏逐栏设置，与字效工作区的配置参数相同）、特效的开关、作用位置与时长、水平/垂直位置（百分比，与 IMS 文字一致）与关键词；
 * 滤镜与转场调强度，视频特效调位置、缩放、旋转与不透明度，并设置起点或所占交界处。
 * 颜色、速度与动画外观等资产内部参数不在这里调，说明原因并提供回字效工作区的入口；这些参数已有的覆盖仍生效，可在此恢复默认。
 * 修改只进入草稿，随模板保存时服务端再按发布契约检查。
 */
import { useState } from "react";
import { Bold, Italic, RotateCcw, Strikethrough, Trash2, Underline, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Hint } from "@/components/Hint";
import { ParameterControl, withLine, type ParameterValue } from "@/components/ParameterControl";
import { SpriteKind } from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "@/features/sprites/model";
import { CATEGORIES, EFFECT_PARAMETER, TEMPLATE_KEYWORD_PARAMETERS, categoryOf, contentLines, instanceTransformFields, isInstanceParameter, percentToPixels, pixelsToPercent, transformField, type TransformField } from "@/features/sprites/parameters";
import { textKey, textRoles, singleRowText, type EffectTrack, type RemotionClip } from "./model";
import { BOUND_TEXT_PARAMETERS, type bindClip } from "./clips";
import { TrackTiming } from "./TrackTiming";
import { trackLabel, type ResolvedTrack } from "./tracks";

/** 关键词页签里的参数：关键词与六个样式选项；字效自带的关键词效果参数属于资产本身，不在模板里修改。 */
const isKeywordParameter = (key: string) => TEMPLATE_KEYWORD_PARAMETERS.includes(key);
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

/** 示例文字允许的最大长度，与 IMS 标题、字幕一致。 */
const SAMPLE_MAX_LENGTH = { title: 60, subtitle: 100 } as const;

/** 选中片段的右栏；资产已不在目录中时只允许移除。调用方按片段 ID 设 key，切换片段时页签回到「外观与位置」。 */
export function SpriteClipInspector({ clip, asset, duration, textTrack, textTracks, textResolved, onSample, onEnglishSample, onTextTiming, onOpenRemotion, onParameter, onTransform, onStart, onBind, onRemove, onClose }: {
  clip: RemotionClip;
  asset: SpriteAsset | undefined;
  duration: number | undefined;
  /** 字效作用的标题或字幕对象：示例文字与时间属于它，字效只是显示它。 */
  textTrack?: EffectTrack;
  textTracks?: EffectTrack[];
  textResolved?: ResolvedTrack;
  onSample: (value: string) => void;
  /** 双语字效下栏的英文示例文字：写进所作用对象的英文文字对象。 */
  onEnglishSample?: (value: string) => void;
  onTextTiming: (timing: Pick<EffectTrack, "start_mode" | "start" | "duration">) => void;
  /** 回到字效工作区修改资产本身；没有入口时不显示按钮。 */
  onOpenRemotion?: () => void;
  onParameter: (key: string, value: ParameterValue | undefined) => void;
  onTransform: (patch: Partial<Record<TransformField, number | undefined>>) => void;
  onStart: (start: number) => void;
  onBind: (binding: Parameters<typeof bindClip>[2]) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"appearance" | "timing" | "keyword">("appearance");
  const isText = asset?.kind === SpriteKind.TEXT;
  const isTransition = asset?.kind === SpriteKind.TRANSITION_OVERLAY;
  const legacyTextTarget = clip.business_target === "title" || clip.business_target === "subtitle" ? clip.business_target : undefined;
  // 双语字效（双语字幕）自己显示中英两种语言：只能作用于中文对象，示例文字中英各一行（下栏取英文文字对象的示例）。
  const bilingual = isText && asset?.bilingual === true && textTrack?.language !== "en";
  // 作用于文字时，文字与关键词由该文字对象提供，不在这里单独设置。
  const shown = (key: string) => !(clip.text_target || legacyTextTarget) || !BOUND_TEXT_PARAMETERS.includes(key);
  // 只有字效把关键词与六个样式选项移到单独页签；关键词效果参数与其他类型里以 keyword 开头的参数都按资产内部参数处理。
  const keywordParameters = asset && isText ? asset.parameters.filter(({ field }) => isKeywordParameter(field.key) && shown(field.key)) : [];
  const styleSwitches = keywordParameters.flatMap((item) => {
    const style = KEYWORD_STYLES.find(([key]) => key === item.field.key);
    return item.field.kind === "boolean" && style ? [{ ...item, label: style[1], Icon: style[2] }] : [];
  });
  const colorItem = keywordParameters.find(({ field }) => field.key === "keywordColor" && field.kind === "string");
  const sizeItem = keywordParameters.find(({ field }) => field.key === "keywordSize" && field.kind === "integer");
  const legacyText = isText && KEYWORD_STYLE_KEYS.some((key) => !keywordParameters.some(({ field }) => field.key === key));
  const fontSize = asset?.parameters.find(({ field }) => field.key === "fontSize");
  // 模板里只开放适用的参数；资产内部参数（颜色、速度、动画时间等）回字效工作区改，已有覆盖仍列出以便恢复默认。
  const others = asset?.parameters.filter((item) => !keywordParameters.includes(item) && shown(item.field.key)) ?? [];
  const appearanceParameters = asset ? others.filter(({ field, group }) => isInstanceParameter(asset.kind, field.key, group)) : [];
  // 字效特效的开关、位置与时长单列一组，排在排版与位置之后：同一个特效（effect{k}…）的参数放在一张卡片里，自由组件同一分组的样式参数
  // 也是一张卡片（以分组名为标题）。
  const cardName = ({ field, group }: NonNullable<typeof asset>["parameters"][number]) => group || field.key.match(/^effect\d+/)?.[0];
  const effectParameters = appearanceParameters.filter((item) => item.group || EFFECT_PARAMETER.test(item.field.key));
  const effectCards = [...new Set(effectParameters.map(cardName))]
    .map((name) => ({ title: effectParameters.find((item) => item.group === name) ? name : undefined, rows: effectParameters.filter((item) => cardName(item) === name) }));
  const internalParameters = others.filter((item) => !appearanceParameters.includes(item));
  const exposedTransforms = asset ? instanceTransformFields(asset.kind) : [];
  const overriddenInternal = [
    ...internalParameters.filter(({ field }) => Object.prototype.hasOwnProperty.call(clip.parameters, field.key)).map(({ field }) => ({ key: field.key, label: field.label, restore: () => onParameter(field.key, undefined) })),
    // 目录尚未读到（或资产已被删除）时没有类型与画布可用来命名这些覆盖，等资产出现后再列出。
    ...(asset ? (["x", "y", "scale", "rotation", "opacity"] as TransformField[]).filter((name) => !exposedTransforms.includes(name) && clip.transform[name] !== undefined)
      .map((name) => { const { label } = transformField(name, asset.kind, asset.width, asset.height); return { key: name, label, restore: () => onTransform({ [name]: undefined }) }; }) : []),
  ];
  // 字效的时间跟随所作用的文字对象：标题可在这里设置，字幕固定为整段视频。
  const timedText = isText && textTrack?.target === "title" && textTrack.language !== "en" ? textTrack : undefined;
  const tabs = [["appearance", "外观与位置"], ...(timedText ? [["timing", "时间设置"] as const] : []), ["keyword", "关键词设置"]] as const;
  const activeTab = isText && (tab !== "timing" || timedText) ? tab : "appearance";
  // 字效可作用的文字对象；双语字效已显示中英两种语言，只列中文对象。
  const textOptions = textTracks
    ? textTracks.filter((track) => (track.target === "title" || track.target === "subtitle") && (!bilingual || track.language !== "en"))
      .map((track) => ({ value: textKey(track.target, track.language), label: trackLabel(track, textTracks), target: track.target as "title" | "subtitle", language: track.language }))
    : (["title", "subtitle"] as const).map((target) => ({ value: target, label: textRoles[target], target, language: undefined }));
  const sampleRole = textTrack?.target === "title" || textTrack?.target === "subtitle" ? textTrack.target : undefined;
  // 英文标题和字幕固定单行：即使资产是双栏字效，作用在英文上也只用它的上栏，因此示例文字只填一行。
  const singleRow = isText && textTrack?.language === "en" && (sampleRole === "title" || sampleRole === "subtitle");
  // 双语字效的下栏取所作用对象的英文文字对象：它的示例译文在这里填写，合成时同样由切片译文替换。
  const englishTrack = bilingual && sampleRole ? textTracks?.find((track) => track.target === sampleRole && track.language === "en") : undefined;
  // 多行字效逐行填写示例：字幕的连续切片逐行出现，标题按其中的换行分栏，与合成一致。
  const sampleLines = sampleRole && isText ? (singleRow ? 1 : contentLines(asset)) : 1;
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
      {changed && <span className="mb-2.5 shrink-0 text-[10px] text-primary">已覆盖</span>}
      {changed && <Hint label={`恢复默认：${field.label}`}><Button type="button" variant="ghost" size="icon" className="mb-0.5 size-8 shrink-0" aria-label={`恢复默认：${field.label}`} onClick={() => onParameter(field.key, undefined)}><RotateCcw className="size-3.5" aria-hidden="true" /></Button></Hint>}
    </div>;
  };
  const category = asset ? CATEGORIES.find((item) => item.id === categoryOf(asset.kind))?.label : undefined;
  return <section aria-label="AI 资产设置" className="min-w-0">
    <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3">
      <div className="min-w-0"><h2 className="truncate text-[13px] font-semibold leading-5">{asset?.name ?? "未找到的资产"}</h2><p className="mt-0.5 text-[11px] leading-3 text-muted-foreground">{category ?? "资产已不可用，保存前请移除"}</p></div>
      <Hint label="关闭资产设置"><Button type="button" variant="ghost" size="icon" className="size-6 shrink-0" aria-label="关闭资产设置" onClick={onClose}><X className="size-3.5" aria-hidden="true" /></Button></Hint>
    </div>
    {asset && isText && <div role="tablist" aria-label="资产设置分类" className={`grid border-b px-4 ${tabs.length === 3 ? "grid-cols-3" : "grid-cols-2"}`}>
      {tabs.map(([value, label]) =>
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
        <legend className="sr-only">当前模板参数覆盖</legend>
        <div><h3 className="text-xs font-medium">当前模板参数覆盖</h3><p className="mt-0.5 text-[11px] text-muted-foreground">只影响这次摆放，不改资产默认值，也不影响其他模板。</p></div>
        {sampleRole && textTrack && (bilingual ? <fieldset className="space-y-1.5">
          <legend className="text-xs">{sampleLines > 1 ? "示例文字（两组中英字幕）" : "示例文字（中文、英文各一行）"}</legend>
          {Array.from({ length: sampleLines }, (_, index) => <div key={index} className="space-y-1.5">
            <Input aria-label={sampleLines > 1 ? `示例文字（中文）第 ${index + 1} 行` : "示例文字（中文）"} placeholder="中文"
              value={sampleLines > 1 ? textTrack.editor[sampleRole].split("\n")[index] ?? "" : singleRowText(textTrack.editor[sampleRole])}
              maxLength={Math.floor((SAMPLE_MAX_LENGTH[sampleRole] - sampleLines + 1) / sampleLines)}
              onChange={(event) => onSample(sampleLines > 1 ? withLine(textTrack.editor[sampleRole], sampleLines, index, event.target.value) : event.target.value)} />
            <Input aria-label={sampleLines > 1 ? `示例文字（英文）第 ${index + 1} 行` : "示例文字（英文）"} placeholder="English"
              value={englishTrack ? sampleLines > 1 ? englishTrack.editor[sampleRole].split("\n")[index] ?? "" : singleRowText(englishTrack.editor[sampleRole]) : ""}
              maxLength={Math.floor((SAMPLE_MAX_LENGTH[sampleRole] - sampleLines + 1) / sampleLines)} disabled={!englishTrack || !onEnglishSample}
              onChange={(event) => onEnglishSample?.(sampleLines > 1 ? withLine(englishTrack?.editor[sampleRole] ?? "", sampleLines, index, event.target.value) : event.target.value)} />
          </div>)}
          <p className="text-[11px] text-muted-foreground">{sampleLines > 1 ? "上下两组各有中文与对应英文，保留各组样式。合成时按同句的连续切片成组，每组中英文同步出现。" : "双语字效上下两栏是同一句的中文与英文：合成时换成每条切片的中文与译文，两种语言同时出现。"}</p>
          {!englishTrack && <p className="text-[11px] text-destructive">还没有英文{sampleRole === "title" ? "标题" : "字幕"}：请在下方开启中英双语并填写英文示例文字，否则保存时会被拒绝。</p>}
        </fieldset> : sampleLines > 1 ? <fieldset className="space-y-1.5">
          <legend className="text-xs">示例文字（{sampleLines} 行）</legend>
          {Array.from({ length: sampleLines }, (_, index) =>
            <Input key={index} aria-label={`示例文字第 ${index + 1} 行`} placeholder={`第 ${index + 1} 行`} value={textTrack.editor[sampleRole].split("\n")[index] ?? ""}
              maxLength={Math.floor((SAMPLE_MAX_LENGTH[sampleRole] - (sampleLines - 1)) / sampleLines)}
              onChange={(event) => onSample(withLine(textTrack.editor[sampleRole], sampleLines, index, event.target.value))} />)}
          <p className="text-[11px] text-muted-foreground">{sampleRole === "subtitle"
            ? `合成时同一句里连续的 ${sampleLines} 条字幕逐行出现，最后一条结束后一起清空，再显示下一组。`
            : "合成时实际标题按其中的换行分行（多出的并入最后一行），没有换行时整段显示在第一行。"}</p>
        </fieldset> : <div className="space-y-1.5">
          <Label htmlFor={`${textTrack.id}-sample`} className="text-xs">示例文字</Label>
          <Input id={`${textTrack.id}-sample`} value={singleRow ? singleRowText(textTrack.editor[sampleRole]) : textTrack.editor[sampleRole]} maxLength={SAMPLE_MAX_LENGTH[sampleRole]} onChange={(event) => onSample(event.target.value)} />
          <p className="text-[11px] text-muted-foreground">{textRoles[sampleRole]}的文字由这个文字对象提供，合成时换成实际{sampleRole === "title" ? "标题" : "字幕"}。</p>
          {singleRow && (asset?.lines ?? 1) > 1 && <p className="text-[11px] text-muted-foreground">英文{sampleRole === "title" ? "标题" : "字幕"}固定单行：这个字效是双栏，作用在英文上只用上栏显示，下栏的字体、字重、字号比例与栏间距不参与，因此中英双语不会出现四行。</p>}
        </div>)}
        {appearanceParameters.filter((item) => !effectParameters.includes(item)).map(parameterRow)}
        {exposedTransforms.map((name) => {
          const { fallback, ...field } = transformField(name, asset.kind, asset.width, asset.height);
          // 位置像 IMS 文字一样用 0～100% 表示，存储仍是资产画布像素。
          const total = name === "x" ? asset.width : asset.height;
          const percent = name === "x" || name === "y";
          const stored = clip.transform[name];
          const current = stored ?? fallback(asset.box);
          return <div key={name} className="flex items-end gap-1.5">
            <ParameterControl className="min-w-0 flex-1" disabled={false}
              field={percent ? { ...field, label: name === "x" ? "水平位置 %" : "垂直位置 %", minimum: 0, maximum: 100 } : field}
              value={percent ? pixelsToPercent(current, total) : current}
              onChange={(value) => onTransform({ [name]: percent ? percentToPixels(value as number, total) : value as number })} />
            {stored !== undefined && <span className="mb-2.5 shrink-0 text-[10px] text-primary">已覆盖</span>}
            {stored !== undefined && <Hint label={`恢复默认：${field.label}`}><Button type="button" variant="ghost" size="icon" className="mb-0.5 size-8 shrink-0" aria-label={`恢复默认：${field.label}`} onClick={() => onTransform({ [name]: undefined })}><RotateCcw className="size-3.5" aria-hidden="true" /></Button></Hint>}
          </div>;
        })}
        {exposedTransforms.includes("x") && <p className="text-[11px] text-muted-foreground">也可以在预览画面上拖动所选资产调整位置。</p>}
        {effectCards.length > 0 && <div className="space-y-2">
          <h3 className="text-xs font-medium">特效</h3>
          {effectCards.map(({ title, rows }) => <section key={rows[0].field.key} aria-label={title} className="space-y-2 rounded-md border px-3 py-2">
            {title && <h4 className="text-[11px] font-medium text-muted-foreground">{title}</h4>}
            {rows.map(parameterRow)}
          </section>)}
        </div>}
        {overriddenInternal.length > 0 && <div className="space-y-1.5 rounded-md border px-3 py-2">
          <p className="text-[11px] text-muted-foreground">下列设置不在模板里调整，但这次摆放仍带着覆盖值：</p>
          {overriddenInternal.map((item) => <div key={item.key} className="flex items-center justify-between gap-2 text-xs"><span className="min-w-0 truncate">{item.label} · 已覆盖</span>
            <Button type="button" variant="ghost" size="sm" className="h-6 shrink-0 px-2 text-[11px]" aria-label={`恢复默认：${item.label}`} onClick={item.restore}>恢复默认</Button></div>)}
        </div>}
      </fieldset>
      <fieldset hidden={activeTab !== "appearance"} className="space-y-2 border-t px-4 py-3">
        <legend className="sr-only">资产默认参数</legend>
        <h3 className="text-xs font-medium">资产默认参数</h3>
        <p className="text-[11px] text-muted-foreground">颜色、速度与动画外观等属于资产本身，不在模板里逐次调整。需要修改请到字效工作区生成新版本并重新保存到资产；已放进模板的这个资产不会被静默改变。</p>
        {internalParameters.length > 0 && <p className="text-[11px] text-muted-foreground">资产开放的参数：{internalParameters.map(({ field }) => field.label).join("、")}</p>}
        {onOpenRemotion && <Button type="button" variant="outline" size="sm" className="h-7 text-[11px]" onClick={onOpenRemotion}>前往字效工作区</Button>}
      </fieldset>
    </>}
    {timedText && <div hidden={activeTab !== "timing"} className="border-t"><TrackTiming track={timedText} duration={duration ?? 0} resolved={textResolved} onChange={onTextTiming} /></div>}
    <div className="space-y-3 border-t px-4 py-3">
      {isText ? <div className="space-y-2">
        <h3 className="text-xs font-medium">作用对象</h3>
        <ToggleGroup type="single" variant="outline" size="sm" className="w-full flex-wrap" aria-label="字效作用对象" value={clip.text_target ? textKey(clip.text_target, clip.language) : ""}
          onValueChange={(value) => { const option = textOptions.find((item) => item.value === value); if (option) onBind({ text_target: option.target, ...(option.language ? { language: option.language } : {}) }); }}>
          {textOptions.map((option) => <ToggleGroupItem key={option.value} value={option.value} className="flex-1 text-xs">{option.label}</ToggleGroupItem>)}
        </ToggleGroup>
        <p className="text-[11px] text-muted-foreground">{clip.text_target ? `由这个字效显示${textRoles[clip.text_target]}${bilingual ? sampleLines > 1 ? "的两组中英字幕" : "的中英两行（上栏中文、下栏英文）" : ""}：文字与关键词取自${textRoles[clip.text_target]}${bilingual ? "和它的英文译文" : ""}，时间跟随它。` : legacyTextTarget ? `当前与 IMS ${textRoles[legacyTextTarget]}同时显示。重新选择作用对象后，将改为仅显示 Remotion 字效。` : "请选择作用的标题或底部字幕，否则无法保存模板。"}</p>
        {bilingual && <p className="text-[11px] text-muted-foreground">双语字效自己显示中英两种语言，所以中英文的 IMS 文字都不再显示，也只能作用于中文对象；英文{clip.text_target === "title" ? "标题" : "字幕"}的启用与文字由下方「中英双语」与英文示例文字提供。</p>}
      </div> : isTransition ? <div className="space-y-2">
        <h3 className="text-xs font-medium">转场 {clip.transition_boundary}</h3>
        <p className="text-[11px] text-muted-foreground">使用资产自身持续时间，选择方式在「镜头转场」中设置。</p>
      </div> : <ParameterControl field={{ key: "start", label: "开始时间（秒）", kind: "number", minimum: 0, maximum: duration }} value={clip.start} disabled={false}
        onChange={(value) => onStart(value as number)} />}
      <Button type="button" variant="outline" className="w-full gap-1.5" onClick={onRemove}><Trash2 className="size-3.5" aria-hidden="true" />移除这个资产</Button>
    </div>
  </section>;
}
