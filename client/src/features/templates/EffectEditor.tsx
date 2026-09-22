/** 选中对象的参数面板：编辑文字、动画、画面效果与转场，变更直接传回模板草稿。 */
import { useId } from "react";
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
    <div className="space-y-2">
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
    <div className="space-y-2">
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
          <SelectContent>
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

/** 仅显示选中对象的参数；循环与入出场动画相互排斥，其他对象的草稿保持不变。 */
export function EffectEditor({
  draft,
  catalog,
  onChange,
  target,
  onClose,
  onRemove,
}: {
  draft: EffectDraft;
  catalog: EffectAsset[];
  onChange: (draft: EffectDraft) => void;
  target: EffectTarget;
  onClose: () => void;
  onRemove?: () => void;
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
    <section aria-label="特效设置" className="min-w-0 space-y-5 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1"><h2 className="font-semibold">特效设置</h2><p className="text-sm text-muted-foreground" aria-live="polite">{effectTargets[target]}</p></div>
        <Button type="button" variant="ghost" size="icon" className="size-7 shrink-0" aria-label="关闭特效设置" title="关闭特效设置" onClick={onClose}><X aria-hidden="true" /></Button>
      </div>
      {(Object.keys(textRoles) as TextRole[]).filter((role) => role === target).map((role) => {
        const textKey = role === "bubble" ? "bubbleText" : role;
        const styleKey =
          role === "bubble" ? "bubble" : (`${role}Flower` as const);
        return (
          <div key={role} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor={`${id}-${role}`}>示例文字</Label>
              <Input
                id={`${id}-${role}`}
                value={editor[textKey]}
                maxLength={
                  role === "title" ? 60 : role === "subtitle" ? 100 : 40
                }
                onChange={(event) => update(textKey, event.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
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
                className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-3"
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
            <p className="text-xs text-muted-foreground">
              循环与入场、出场动画互斥。选择“无效果”后可切换。
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={() => onChange(resetTextTarget(draft, role))}
            >
              重置特效设置
            </Button>
          </div>
        );
      })}
      {(target === "filter" || target === "vfx") && <div className="space-y-5">
        {selector(target, effectTargets[target])}
        <p className="text-xs text-muted-foreground">效果作用于完整画面，在当前轨道的时间范围内生效。</p>
      </div>}
      {target === "transition" && <div className="space-y-5">
        {selector("transition", "镜头转场")}
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
      <Button type="button" variant="outline" className="w-full" onClick={() => { if (onRemove) onRemove(); else onChange(removeTarget(draft, target)); onClose(); }}>移除当前画面对象</Button>
    </section>
  );
}
