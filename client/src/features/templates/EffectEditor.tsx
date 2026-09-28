/** 选中对象的参数面板：编辑文字、动画、画面效果与转场，变更直接传回模板草稿。 */
import { useId, useState } from "react";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
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

/** 带关联标签的数值控件，空值暂记 NaN，交由表单和服务端校验阻止保存。 */
function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
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
      />
    </div>
  );
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
        <Select
          value={editor[field] || "none"}
          onValueChange={(value) => onChange(value === "none" ? "" : value)}
          disabled={disabled || !catalog.length}
        >
          <SelectTrigger id={id} className="w-full min-w-0">
            <SelectValue placeholder="无效果" />
          </SelectTrigger>
          <SelectContent className="template-inspector-select">
            <SelectItem value="none">无效果</SelectItem>
            {editor[field] && !selected && (
              <SelectItem value={editor[field]}>
                未载入：{editor[field]}
              </SelectItem>
            )}
            {options.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
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
  const id = useId();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const assets = catalog.filter((asset) => asset.category === "transition/normal");
  const current = assets.find((asset) => asset.id === value);
  const choices = assets.filter((asset) => `${asset.name} ${asset.effect_id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <div className="space-y-2">
    <p className="text-xs font-medium text-muted-foreground">转场类型</p>
    <div className="template-transition-card flex min-w-0 items-center gap-2.5 rounded-lg border px-2.5 py-2.5">
      <span className="template-transition-swatch h-10 w-14 shrink-0 rounded-md" aria-hidden="true" />
      <div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold">{current?.name ?? (value || "未设置转场")}</p><p className="mt-1 truncate text-[10px] text-muted-foreground">转场 · {current?.effect_id ?? (value || "请选择")}</p></div>
      <Button type="button" variant="ghost" size="sm" className="h-7 shrink-0 px-2 text-[11px]" aria-expanded={open} aria-controls={id} onClick={() => setOpen((shown) => !shown)}>更换</Button>
    </div>
    {open && <div id={id} className="template-transition-choices space-y-2 rounded-lg border p-2">
      <Input type="search" aria-label="搜索转场" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称或编号" className="h-8 text-xs" />
      <div role="group" aria-label="可选转场" className="max-h-48 space-y-1 overflow-y-auto">{choices.map((asset) => <Button key={asset.id} type="button" variant="ghost" aria-label={`选择转场：${asset.name}`} aria-pressed={value === asset.id} onClick={() => { onChange(asset.id); setOpen(false); setSearch(""); }} className="h-auto w-full min-w-0 justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[11px]"><span className="truncate">{asset.name}</span><span className="shrink-0 text-[10px] text-muted-foreground">{asset.effect_id}</span></Button>)}</div>
      {!choices.length && <p role="status" className="px-2 py-3 text-xs text-muted-foreground">没有匹配的转场</p>}
    </div>}
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
        <Button type="button" variant="ghost" size="icon" className="size-7 shrink-0" aria-label="关闭特效设置" title="关闭特效设置" onClick={onClose}><X aria-hidden="true" /></Button>
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
                <div className="grid grid-cols-2 gap-2">
                  {([
                    [`${role}KeywordBold`, "加粗"],
                    [`${role}KeywordItalic`, "斜体"],
                    [`${role}KeywordUnderline`, "下划线"],
                    [`${role}KeywordStrikeout`, "删除线"],
                  ] as const).map(([key, label]) => (
                    <label key={key} className="template-keyword-option flex min-h-9 items-center gap-2 rounded-md border px-2.5 text-xs">
                      <input type="checkbox" checked={editor[key]} onChange={(event) => update(key, event.target.checked)} />
                      {label}
                    </label>
                  ))}
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={Boolean(editor[`${role}KeywordColor`])} onChange={(event) => update(`${role}KeywordColor`, event.target.checked ? "#FFFF00" : "")} />
                    设置关键词颜色
                  </label>
                  <div className="flex items-center gap-3">
                    <Label htmlFor={`${id}-keyword-color`}>关键词颜色</Label>
                    <input id={`${id}-keyword-color`} type="color" value={editor[`${role}KeywordColor`] || "#FFFF00"} disabled={!editor[`${role}KeywordColor`]} onChange={(event) => update(`${role}KeywordColor`, event.target.value.toUpperCase())} className="h-9 w-14 rounded border bg-background p-1 disabled:cursor-not-allowed" />
                    <span className="text-xs text-muted-foreground">{editor[`${role}KeywordColor`] || `保持${role === "title" ? "标题" : "字幕"}原色`}</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={editor[`${role}KeywordSize`] !== 0} onChange={(event) => update(`${role}KeywordSize`, event.target.checked ? editor[`${role}Size`] : 0)} />
                    设置关键词字号
                  </label>
                  <NumberField
                    label="关键词字号"
                    value={editor[`${role}KeywordSize`] || editor[`${role}Size`]}
                    min={12}
                    max={300}
                    disabled={editor[`${role}KeywordSize`] === 0}
                    onChange={(value) => update(`${role}KeywordSize`, value)}
                  />
                </div>
              </fieldset>
            )}
            {view !== "keyword" && <><div className="grid grid-cols-2 gap-2">
              <NumberField
                label="字号"
                value={editor[`${role}Size`]}
                min={12}
                max={300}
                onChange={(value) => update(`${role}Size`, value)}
              />
              <NumberField
                label="水平位置 %"
                value={editor[`${role}X`]}
                min={0}
                max={100}
                step={0.1}
                onChange={(value) => update(`${role}X`, value)}
              />
              <NumberField
                label="垂直位置 %"
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
