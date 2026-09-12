/** 效果设置面板：以 shadcn/ui 控件编辑三个文字角色、画面和转场，不发起 API 请求。 */
import { useId } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  defaultEditor,
  effectGroups,
  textRoles,
  type Draft,
  type Editor,
  type EffectAsset,
  type EffectKey,
  type TextRole,
} from "./model";

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

/** 切换页签不丢失其他角色配置；循环与入出场动画通过禁用相互排斥。 */
export function EffectEditor({
  draft,
  catalog,
  onChange,
}: {
  draft: Draft;
  catalog: EffectAsset[];
  onChange: (draft: Draft) => void;
}) {
  const id = useId();
  const editor = draft.editor;
  const update = <K extends keyof Editor>(key: K, value: Editor[K]) =>
    onChange({ ...draft, editor: { ...editor, [key]: value } });
  /** 取消动画时修复其无效时长，避免禁用的输入留下无法保存的草稿；有效设置继续保留。 */
  const changeEffects = (values: Partial<Record<EffectKey, string>>) => {
    const next = { ...editor, ...values };
    for (const field of Object.keys(values) as EffectKey[]) {
      if (values[field] || !(field.endsWith("In") || field.endsWith("Out")))
        continue;
      const durationKey = `${field}Duration` as `${TextRole}${"In" | "Out"}Duration`;
      const duration = next[durationKey];
      if (!Number.isFinite(duration) || duration < 0.1 || duration > 3)
        next[durationKey] = defaultEditor[durationKey];
    }
    onChange({ ...draft, editor: next });
  };
  const selector = (field: EffectKey, label: string, disabled = false) => (
    <EffectSelect
      key={field}
      label={label}
      field={field}
      editor={editor}
      catalog={catalog}
      disabled={disabled}
      onChange={(value) => changeEffects({ [field]: value })}
    />
  );
  const clear = (fields: EffectKey[]) =>
    changeEffects(Object.fromEntries(fields.map((field) => [field, ""])));

  return (
    <Tabs defaultValue="title" className="gap-5">
      <TabsList className="grid h-auto w-full grid-cols-3 gap-1 sm:grid-cols-5">
        {Object.entries(textRoles).map(([role, label]) => (
          <TabsTrigger key={role} value={role}>
            {label}
          </TabsTrigger>
        ))}
        <TabsTrigger value="picture">画面</TabsTrigger>
        <TabsTrigger value="transition">转场</TabsTrigger>
      </TabsList>
      {(Object.keys(textRoles) as TextRole[]).map((role) => {
        const textKey = role === "bubble" ? "bubbleText" : role;
        const styleKey =
          role === "bubble" ? "bubble" : (`${role}Flower` as const);
        return (
          <TabsContent key={role} value={role} className="space-y-5">
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
            <div className="grid grid-cols-3 gap-3">
              <NumberField
                label="字号"
                value={editor[`${role}Size`]}
                min={12}
                max={120}
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
                className="grid grid-cols-[minmax(0,1fr)_7rem] gap-3"
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
              onClick={() =>
                clear([styleKey, `${role}In`, `${role}Out`, `${role}Loop`])
              }
            >
              清除本页效果
            </Button>
          </TabsContent>
        );
      })}
      <TabsContent value="picture" className="space-y-5">
        {selector("filter", "视频滤镜")}
        {selector("vfx", "画面特效")}
        <Button
          type="button"
          variant="outline"
          onClick={() => clear(["filter", "vfx"])}
        >
          清除画面效果
        </Button>
      </TabsContent>
      <TabsContent value="transition" className="space-y-5">
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
        <Button
          type="button"
          variant="outline"
          onClick={() => clear(["transition"])}
        >
          清除转场
        </Button>
      </TabsContent>
    </Tabs>
  );
}
