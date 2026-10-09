/** 选中对象的参数面板：编辑文字、动画、画面效果与转场，变更直接传回模板草稿。 */
import { useId } from "react";
import { Bold, Italic, Strikethrough, Underline, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Hint } from "@/components/Hint";
import { EffectCombobox } from "./EffectCombobox";
import {
  effectGroups,
  textRoles,
  type EffectDraft,
  type Editor,
  type EffectAsset,
  type EffectKey,
  type TextRole,
} from "./model";
import { changeEffects, effectTargets, removeTarget, resetTextTarget, type EffectTarget } from "./effects";

/**
 * 带关联标签的数值控件；slider 为 true 时在输入框左侧加滑块用于快速拖动，二者共享同一个值。
 * 半宽布局（动画时长等）不开启滑块。输入框空值暂记 NaN，交由表单和服务端校验阻止保存；此时滑块停在最小值。
 */
function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled = false,
  slider = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
  slider?: boolean;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-3">
        {slider && <Slider
          label={`${label}滑块`}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          value={[Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min]}
          onValueChange={([next]) => onChange(next)}
          className="flex-1"
        />}
        <Input
          id={id}
          type="number"
          required
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          value={Number.isFinite(value) ? value : ""}
          onChange={(event) => onChange(event.target.valueAsNumber)}
          className={slider ? "w-[4.5rem] shrink-0 text-right" : undefined}
        />
      </div>
    </div>
  );
}

/** 标题或字幕的四种关键词局部样式：编辑字段、无障碍名称与图标。 */
function keywordStyles(role: "title" | "subtitle") {
  return [
    [`${role}KeywordBold`, "加粗", Bold],
    [`${role}KeywordItalic`, "斜体", Italic],
    [`${role}KeywordUnderline`, "下划线", Underline],
    [`${role}KeywordStrikeout`, "删除线", Strikethrough],
  ] as const;
}

/** 单个效果选择器；无效果使用独立哨兵值，历史未知 ID 仍可显示并清除。 */
function EffectSelect({
  label,
  field,
  editor,
  catalog,
  onChange,
  disabled = false,
}: {
  label: string;
  field: EffectKey;
  editor: Editor;
  catalog: EffectAsset[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const options = catalog.filter(
    (item) => item.category === effectGroups[field],
  );
  const selected = options.find((item) => item.id === editor[field]);
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-3">
        <EffectCombobox
          id={id}
          value={editor[field] || "none"}
          onChange={(value) => onChange(value === "none" ? "" : value)}
          disabled={disabled || !catalog.length}
          placeholder="无效果"
          searchPlaceholder={`搜索${label}`}
          options={[
            { value: "none", label: "无效果" },
            ...(editor[field] && !selected ? [{ value: editor[field], label: `未载入：${editor[field]}` }] : []),
            ...options.map((item) => ({ value: item.id, label: item.name, hint: item.effect_id })),
          ]}
        />
        {selected?.preview_url && (
          <img
            key={selected.preview_url}
            src={selected.preview_url}
            alt={`${label}示例`}
            className="h-10 w-14 shrink-0 rounded bg-muted object-contain"
            onError={(event) => {
              event.currentTarget.style.visibility = "hidden";
            }}
          />
        )}
      </div>
    </div>
  );
}

/** 当前转场使用预览卡片展示，目录搜索只替换所选转场的效果。 */
function TransitionPicker({ value, catalog, onChange }: { value: string; catalog: EffectAsset[]; onChange: (value: string) => void }) {
  const assets = catalog.filter((asset) => asset.category === "transition/normal");
  const current = assets.find((asset) => asset.id === value);
  return <div className="space-y-2">
    <p className="text-xs font-medium text-muted-foreground">转场类型</p>
    <EffectCombobox value={value} ariaLabel="转场类型" placeholder="未设置转场" searchPlaceholder="搜索转场" onChange={onChange}
      options={assets.map((asset) => ({ value: asset.id, label: asset.name, hint: asset.effect_id }))}>
      <span className="template-transition-card flex min-w-0 items-center gap-2.5 rounded-lg border px-2.5 py-2.5 transition-colors">
        <span className="template-transition-swatch h-10 w-14 shrink-0 rounded-md" aria-hidden="true" />
        <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">{current?.name ?? (value || "未设置转场")}</span><span className="mt-1 block truncate text-[11px] text-muted-foreground">转场 · {current?.effect_id ?? (value || "请选择")}</span></span>
        <span className="shrink-0 text-[11px] text-muted-foreground">更换</span>
      </span>
    </EffectCombobox>
  </div>;
}

/** 仅显示选中对象的参数；循环与入出场动画相互排斥，其他对象的草稿保持不变。 */
export function EffectEditor({
  draft,
  catalog,
  onChange,
  target,
  onClose,
  onRemove,
  view = "all",
  showHeader = true,
}: {
  draft: EffectDraft;
  catalog: EffectAsset[];
  onChange: (draft: EffectDraft) => void;
  target: EffectTarget;
  onClose: () => void;
  onRemove?: () => void;
  view?: "all" | "appearance" | "keyword";
  showHeader?: boolean;
}) {
  const id = useId();
  const editor = draft.editor;
  const update = <K extends keyof Editor>(key: K, value: Editor[K]) =>
    onChange({ ...draft, editor: { ...editor, [key]: value } });
  const selector = (field: EffectKey, label: string, disabled = false) => (
    <EffectSelect
      key={field}
      label={label}
      field={field}
      editor={editor}
      catalog={catalog}
      disabled={disabled}
      onChange={(value) => onChange(changeEffects(draft, { [field]: value }))}
    />
  );

  return (
    <section aria-label="特效设置" className={view === "all" ? "min-w-0 space-y-5 p-4" : "template-inspector-fields min-w-0 space-y-3 p-4"}>
      {showHeader && <div className="flex items-start justify-between gap-2">
        <div className="space-y-1"><h2 className="font-semibold">特效设置</h2><p className="text-sm text-muted-foreground" aria-live="polite">{effectTargets[target]}</p></div>
        <Hint label="关闭特效设置"><Button type="button" variant="ghost" size="icon" className="size-7 shrink-0" aria-label="关闭特效设置" onClick={onClose}><X aria-hidden="true" /></Button></Hint>
      </div>}
      {(Object.keys(textRoles) as TextRole[]).filter((role) => role === target).map((role) => {
        const textKey = role === "bubble" ? "bubbleText" : role;
        const styleKey =
          role === "bubble" ? "bubble" : (`${role}Flower` as const);
        return (
          <div key={role} className={view === "all" ? "space-y-5" : "space-y-3"}>
            {view !== "keyword" && <div className="space-y-2">
              <Label htmlFor={`${id}-${role}`}>示例文字</Label>
              <Input
                id={`${id}-${role}`}
                value={editor[textKey]}
                maxLength={
                  role === "title" ? 60 : role === "subtitle" ? 100 : 40
                }
                onChange={(event) => role === "title"
                  ? onChange({ ...draft, editor: { ...editor, title: event.target.value, titleKeyword: "" } })
                  : update(textKey, event.target.value)}
              />
            </div>}
            {(role === "title" || role === "subtitle") && view !== "appearance" && (
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">关键词样式</legend>
                {/* 四种局部样式可组合，多选切换组按下即开启对应字段。 */}
                <ToggleGroup type="multiple" variant="outline" size="sm" className="w-full"
                  value={keywordStyles(role).filter(([key]) => editor[key]).map(([key]) => key)}
                  onValueChange={(on) => onChange({ ...draft, editor: { ...editor, ...Object.fromEntries(keywordStyles(role).map(([key]) => [key, on.includes(key)])) } })}>
                  {keywordStyles(role).map(([key, label, Icon]) => (
                    <ToggleGroupItem key={key} value={key} aria-label={label} className="flex-1"><Icon aria-hidden="true" /></ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={`${id}-keyword-color-on`}>设置关键词颜色</Label>
                    <Switch id={`${id}-keyword-color-on`} checked={Boolean(editor[`${role}KeywordColor`])} onCheckedChange={(checked) => update(`${role}KeywordColor`, checked ? "#FFFF00" : "")} />
                  </div>
                  <div className="flex items-center gap-3">
                    <Label htmlFor={`${id}-keyword-color`}>关键词颜色</Label>
                    <input id={`${id}-keyword-color`} type="color" value={editor[`${role}KeywordColor`] || "#FFFF00"} disabled={!editor[`${role}KeywordColor`]} onChange={(event) => update(`${role}KeywordColor`, event.target.value.toUpperCase())} className="h-9 w-14 rounded border bg-background p-1 disabled:cursor-not-allowed" />
                    <span className="text-xs text-muted-foreground">{editor[`${role}KeywordColor`] || `保持${role === "title" ? "标题" : "字幕"}原色`}</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={`${id}-keyword-size-on`}>设置关键词字号</Label>
                    <Switch id={`${id}-keyword-size-on`} checked={editor[`${role}KeywordSize`] !== 0} onCheckedChange={(checked) => update(`${role}KeywordSize`, checked ? editor[`${role}Size`] : 0)} />
                  </div>
                  <NumberField
                    label="关键词字号"
                    slider
                    value={editor[`${role}KeywordSize`] || editor[`${role}Size`]}
                    min={12}
                    max={300}
                    disabled={editor[`${role}KeywordSize`] === 0}
                    onChange={(value) => update(`${role}KeywordSize`, value)}
                  />
                </div>
              </fieldset>
            )}
            {view !== "keyword" && <><div className="space-y-3">
              <NumberField
                label="字号"
                slider
                value={editor[`${role}Size`]}
                min={12}
                max={300}
                onChange={(value) => update(`${role}Size`, value)}
              />
              <NumberField
                label="水平位置 %"
                slider
                value={editor[`${role}X`]}
                min={0}
                max={100}
                step={0.1}
                onChange={(value) => update(`${role}X`, value)}
              />
              <NumberField
                label="垂直位置 %"
                slider
                value={editor[`${role}Y`]}
                min={0}
                max={100}
                step={0.1}
                onChange={(value) => update(`${role}Y`, value)}
              />
            </div>
            {selector(styleKey, role === "bubble" ? "气泡样式" : "花字样式")}
            {(["In", "Out"] as const).map((type, index) => (
              <div
                key={type}
                className="grid grid-cols-[minmax(0,1fr)_4.5rem] gap-2"
              >
                {selector(
                  `${role}${type}`,
                  index ? "出场动画" : "入场动画",
                  !!editor[`${role}Loop`],
                )}
                <NumberField
                  label="时长 / 秒"
                  value={editor[`${role}${type}Duration`]}
                  min={0.1}
                  max={3}
                  step={0.1}
                  disabled={!editor[`${role}${type}`]}
                  onChange={(value) => update(`${role}${type}Duration`, value)}
                />
              </div>
            ))}
            {selector(
              `${role}Loop`,
              "循环动画",
              !!(editor[`${role}In`] || editor[`${role}Out`]),
            )}
            {view === "all" && <p className="text-xs text-muted-foreground">循环与入场、出场动画互斥。选择“无效果”后可切换。</p>}
            </>}
            <Button
              type="button"
              variant="outline"
              className="template-inspector-reset w-full"
              onClick={() => onChange(resetTextTarget(draft, role))}
            >
              重置特效设置
            </Button>
          </div>
        );
      })}
      {view !== "keyword" && (target === "filter" || target === "vfx") && <div className="space-y-5">
        {selector(target, effectTargets[target])}
        <p className="text-xs text-muted-foreground">效果作用于完整画面，在当前轨道的时间范围内生效。</p>
      </div>}
      {view !== "keyword" && target === "transition" && <div className="space-y-5">
        {view === "appearance" ? <TransitionPicker value={editor.transition} catalog={catalog} onChange={(value) => onChange(changeEffects(draft, { transition: value }))} /> : selector("transition", "镜头转场")}
        <NumberField
          label="转场时长 / 秒"
          value={draft.transition_duration_seconds}
          min={0.1}
          max={3}
          step={0.1}
          onChange={(value) =>
            onChange({ ...draft, transition_duration_seconds: value })
          }
        />
      </div>}
      <Button type="button" variant="outline" className="template-inspector-remove w-full" onClick={() => { if (onRemove) onRemove(); else onChange(removeTarget(draft, target)); onClose(); }}>移除当前画面对象</Button>
    </section>
  );
}
