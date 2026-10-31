/** 根据成功版本 schema 生成表单：文字组合的字效分为「文字」与「特效」，其他组合版本分为顶层参数与各实例参数，旧版本按图层分组；控件草稿校验后才影响实时预览。 */
import { cn } from "@/lib/utils";
import { useEffect, useId, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ParameterControl, type ParameterField, type ParameterValue } from "@/components/ParameterControl";
import {
  controlLabel,
  instanceTitle,
  isJsonValue,
  keywordDefaultProblem,
  sameJson,
  numericRules,
  schemaGroups,
  textSections,
  effectSections,
  validValue,
  type Control,
  type EffectCard,
  type InstanceFields,
  type Scalar,
  type JsonValue,
  type Values,
  type Version,
} from "./model";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

/** 保留用户清空数字或输入半个色值的编辑过程，防止 NaN 或无效颜色进入渲染器。 */
function Parameter({
  control,
  value,
  disabled,
  onChange,
}: {
  control: Control;
  value: Scalar;
  disabled: boolean;
  onChange: (value: Scalar) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(String(value));
  const [invalid, setInvalid] = useState(false);
  const label = controlLabel(control);
  const numeric = control.type === "number" || control.type === "integer";
  const rules = numericRules(control);
  const scale = numeric && rules.percent ? 100 : 1;
  useEffect(() => {
    setDraft(
      String(
        typeof value === "number" ? Number((value * scale).toFixed(4)) : value,
      ),
    );
    setInvalid(false);
  }, [value, scale]);
  /** 非法中间输入只留在表单，禁用期间拒绝程序触发的变更。 */
  function edit(text: string) {
    if (disabled) return;
    setDraft(text);
    const next = numeric ? Number(text) / scale : text;
    const valid = text !== "" && validValue(control, next);
    setInvalid(!valid);
    if (valid && next !== value) onChange(next);
  }
  /** 失焦或 Enter 只处理非法中间输入；合法值已实时进入本地预览。 */
  function commit(text = draft) {
    if (disabled) return;
    const next = numeric ? Number(text) / scale : text;
    if (text !== "" && validValue(control, next)) {
      if (next !== value) onChange(next);
    } else {
      setDraft(String(typeof value === "number" ? value * scale : value));
      setInvalid(false);
    }
  }
  const color = (control["x-imv-target"] ?? "").endsWith("/color");
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
        {rules.percent && numeric ? "（%）" : ""}
      </Label>
      {control.enum ? (
        <Select
          disabled={disabled}
          value={String(value)}
          onValueChange={(text) => {
            const next = control.enum!.find(
              (option) => String(option) === text,
            );
            if (next !== undefined) onChange(next);
          }}
        >
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {control.enum.map((option) => (
              <SelectItem key={String(option)} value={String(option)}>
                {(
                  {
                    left: "左对齐",
                    center: "居中",
                    right: "右对齐",
                    "400": "常规",
                    "700": "粗体",
                  } as Record<string, string>
                )[String(option)] || String(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : control.type === "boolean" ? (
        <Switch
          id={id}
          checked={!!value}
          disabled={disabled}
          onCheckedChange={(checked) => onChange(checked)}
        />
      ) : (
        <div className="flex items-center gap-3">
          {color && (
            <input
              type="color"
              aria-label={`${label}选择器`}
              value={String(value).slice(0, 7)}
              disabled={disabled}
              onChange={(event) =>
                commit(
                  event.target.value +
                    (String(value).length === 9 ? String(value).slice(7) : ""),
                )
              }
              className="size-9 shrink-0 cursor-pointer rounded border bg-transparent p-1"
            />
          )}
          {numeric && rules.min !== undefined && rules.max !== undefined && (
            <Slider
              label={`${label}滑块`}
              min={rules.min * scale}
              max={rules.max * scale}
              step={rules.step * scale}
              value={[
                draft === "" || invalid ? Number(value) * scale : Number(draft),
              ]}
              disabled={disabled}
              onValueChange={([next]) => edit(String(next))}
              onValueCommit={([next]) => commit(String(next))}
              className="flex-1"
            />
          )}
          <Input
            id={id}
            type={numeric ? "number" : "text"}
            value={draft}
            disabled={disabled}
            aria-invalid={invalid}
            min={rules.min === undefined ? undefined : rules.min * scale}
            max={rules.max === undefined ? undefined : rules.max * scale}
            step={numeric ? rules.step * scale : undefined}
            maxLength={control.maxLength ?? 2000}
            onChange={(event) => edit(event.target.value)}
            onBlur={() => commit()}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                commit();
              }
            }}
            className={cn(numeric ? "w-24 shrink-0 text-right" : "min-w-0")}
          />
        </div>
      )}
      {invalid && (
        <p className="text-xs text-destructive" role="alert">
          请输入有效的{label}。
        </p>
      )}
    </div>
  );
}

/** 控件由已验收 schema 决定，结构与动画修改通过聊天发起。 */
export function ParametersPanel({
  version,
  values,
  disabled,
  pending,
  dirty,
  saving,
  readOnly = false,
  onChange,
  onSave,
  onDiscard,
}: {
  version: Version | null;
  values: Values;
  disabled: boolean;
  pending: boolean;
  dirty: boolean;
  saving: boolean;
  readOnly?: boolean;
  onChange: (key: string, value: JsonValue) => void;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  useEffect(() => setInvalid({}), [version?.id]);
  // 关键词样式由云端模板按摆放设置：默认值未关闭（多为这一规则上线前生成的版本）时提前说明，不能保存到资产。
  const keywordProblem = version?.spec.sprite?.kind === "text" ? keywordDefaultProblem(values) : null;
  return (
    <section
      aria-label="模板参数"
      className="@container min-h-0 overflow-y-auto rounded-xl border bg-card p-4"
    >
      <div className="sticky -top-4 z-10 -mx-4 -mt-4 mb-2 space-y-3 border-b bg-card px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <SlidersHorizontal className="size-4" />
          模板参数
          <span
            role="status"
            className="ml-auto text-xs font-normal text-muted-foreground"
          >
            {readOnly
              ? "历史版本 · 只读"
              : saving
                ? version?.spec.schema_version === "2" ? "正在保存…" : "正在保存并检查…"
                : pending
                  ? "正在加载 · 暂不可修改"
                  : dirty
                    ? "有未保存修改"
                    : version
                      ? "已保存"
                      : ""}
          </span>
        </div>
        {version && !readOnly && (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={disabled || !dirty || Object.values(invalid).some(Boolean)} onClick={onSave}>
              保存配置
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || !dirty}
              onClick={onDiscard}
            >
              撤销修改
            </Button>
            {dirty && (
              <p className="text-xs text-muted-foreground">
                预览为本地草稿，保存或撤销后可继续对话和复制。
              </p>
            )}
          </div>
        )}
        {keywordProblem && (
          <p role="status" className="text-xs text-destructive">
            {readOnly
              ? "此历史版本的关键词默认值不是关闭状态，无法保存到资产；请回到最新版本把它们恢复为关闭并保存配置。"
              : keywordProblem}
          </p>
        )}
      </div>
      {!version ? (
        <p className="py-7 text-center text-sm text-muted-foreground">
          模板生成后，在这里调整文字、颜色和位置。
        </p>
      ) : version.spec.schema_version === "2" ? (
        <ComposedParameters version={version} values={values} disabled={disabled} onChange={onChange}
          onValidity={(key, bad) => setInvalid(previous => ({...previous, [key]: bad}))} />
      ) : (
        version.spec.text_layers.map((layer, index) => (
          <details
            key={layer.id}
            open
            className="group border-b pb-5 last:border-b-0"
          >
            <summary className="cursor-pointer py-3 text-sm font-medium">
              文字图层 · {layer.id}
            </summary>
            <div className="grid gap-4 pt-2 @min-[420px]:grid-cols-2">
              {Object.entries(version.candidate.config_schema.properties)
                .filter(([, control]) =>
                  (control["x-imv-target"] ?? "").startsWith(`/text_layers/${index}/`),
                )
                .map(([key, control]) => (
                  <div
                    key={key}
                    className={cn(
                      (control["x-imv-target"] ?? "").endsWith("/text") &&
                        "@min-[420px]:col-span-2",
                    )}
                  >
                    <Parameter
                      control={control}
                      value={values[key] as Scalar}
                      disabled={disabled}
                      onChange={(value) => onChange(key, value)}
                    />
                  </div>
                ))}
            </div>
          </details>
        ))
      )}
    </section>
  );
}

/**
 * 组合版本的参数：文字组合的字效分为「文字」与「特效」两块（`TextBlockParameters`）；其他版本的顶层参数分为宿主固定的「配置」
 * 与这个效果自己的「样式」，各实例参数只在这里调整默认值。保存只检查参数 Schema 并立即生成新版本；无法逐项编辑的实例回退为 JSON 文本域。
 */
function ComposedParameters({version, values, disabled, onChange, onValidity}: {
  version: Version; values: Values; disabled: boolean;
  onChange: (key: string, value: JsonValue) => void; onValidity: (key: string, invalid: boolean) => void;
}) {
  const text = textSections(version) ?? effectSections(version);
  if (text) return <TextBlockParameters sections={text} title={(id) => instanceTitle(version, id)} values={values} disabled={disabled} onChange={onChange} onValidity={onValidity} />;
  const groups = schemaGroups(version.candidate.config_schema.properties ?? {});
  const sections = [["配置", groups.config], ["样式", groups.style]] as const;
  return <div className="space-y-4 py-3">
    <p className="text-xs text-muted-foreground">配置是固定的文字、字体、排版与关键词设置，样式是这个字效自己的外观；云端模板按摆放调整字号、字体、字重、栏间距与逐栏字号比例、位置与关键词，其余在这里修改。保存只检查参数范围，立即生成新版本；布局与时间修改请通过聊天描述。</p>
    {sections.map(([title, all]) => {
      const fields = all.filter((field) => values[field.key] !== undefined);
      return fields.length > 0 && <fieldset key={title} className="space-y-3">
        <legend className="text-sm font-medium">{title}</legend>
        <div className="grid gap-4 @min-[420px]:grid-cols-2">
          {fields.map((field) => <ParameterControl key={field.key} field={field} value={values[field.key] as ParameterValue} disabled={disabled}
            className={cn(field.kind === "string" && "@min-[420px]:col-span-2")}
            onChange={(next) => onChange(field.key, next)} />)}
        </div>
      </fieldset>;
    })}
    {groups.instances.filter(({id}) => values[id] !== undefined).map(({id, fields}) => fields ? (
      <details key={id} className="border-t pt-3">
        <summary className="cursor-pointer text-sm font-medium">{instanceTitle(version, id)}</summary>
        <div className="grid gap-4 pt-3 @min-[420px]:grid-cols-2">
          {fields.filter((field) => (values[id] as Values)[field.key] !== undefined).map((field) => (
            <ParameterControl key={field.key} field={field} value={(values[id] as Values)[field.key] as ParameterValue} disabled={disabled}
              onChange={(next) => onChange(id, {...(values[id] as Values), [field.key]: next})} />
          ))}
        </div>
      </details>
    ) : (
      <JsonParameter key={id} label={id} value={values[id]!} disabled={disabled}
        onChange={(next) => onChange(id, next)} onValidity={(bad) => onValidity(id, bad)} />
    ))}
  </div>;
}

/**
 * 文字组合字效的参数面板：「文字」是文字本身的配置（内容、字号、字体、排版与各栏外观，各栏的内部参数可展开，另有关键词选项）；
 * 「特效」每个特效一张卡片，标题旁的开关决定是否应用，卡片里是它的位置、时长与自己的样式。关掉的特效保留设置、只是不显示。
 */
function TextBlockParameters({sections, title, values, disabled, onChange, onValidity}: {
  sections: NonNullable<ReturnType<typeof textSections>>; title: (id: string) => string; values: Values; disabled: boolean;
  onChange: (key: string, value: JsonValue) => void; onValidity: (key: string, invalid: boolean) => void;
}) {
  /** 一个顶层参数的控件；当前值缺失的参数不显示。 */
  const control = (field: ParameterField) => values[field.key] !== undefined && <ParameterControl key={field.key} field={field} value={values[field.key] as ParameterValue}
    disabled={disabled} className={cn(field.kind === "string" && !field.options && "@min-[420px]:col-span-2")} onChange={(next) => onChange(field.key, next)} />;
  /** 一个实例的内部参数：逐项控件整体回传，形状无法逐项编辑时回退为 JSON。 */
  const nested = ({id, fields}: InstanceFields) => values[id] === undefined ? null : fields ? fields.filter((field) => (values[id] as Values)[field.key] !== undefined).map((field) => (
    <ParameterControl key={`${id}.${field.key}`} field={field} value={(values[id] as Values)[field.key] as ParameterValue} disabled={disabled}
      onChange={(next) => onChange(id, {...(values[id] as Values), [field.key]: next})} />
  )) : <JsonParameter key={id} label={id} value={values[id]!} disabled={disabled} onChange={(next) => onChange(id, next)} onValidity={(bad) => onValidity(id, bad)} />;
  /** 一张特效卡片：开关在标题旁，关掉时卡片内容变淡但仍可调整。 */
  const effect = (card: EffectCard) => {
    const on = card.toggle ? values[card.toggle.key] !== false : true;
    return <section key={card.id} aria-label={card.title} className="space-y-3 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-medium">{card.title}</h4>
        {card.toggle && values[card.toggle.key] !== undefined && <Switch aria-label={`应用：${card.title}`} checked={on} disabled={disabled} onCheckedChange={(checked) => onChange(card.toggle!.key, checked)} />}
      </div>
      <div className={cn("grid gap-4 @min-[420px]:grid-cols-2", !on && "opacity-60")}>
        {card.fields.map(control)}
        {card.instance && nested(card.instance)}
      </div>
    </section>;
  };
  return <div className="space-y-5 py-3">
    <p className="text-xs text-muted-foreground">「文字」是文字本身的内容、字号、字体、排版与外观；「特效」可以逐个选择是否应用，并调整它的位置、时长与外观。云端模板按摆放调整字号、字体、字重、栏间距与逐栏字号比例、特效的开关、位置与时长以及关键词，其余在这里修改。保存只检查参数范围，立即生成新版本；布局修改请通过聊天描述。</p>
    {(sections.text.length > 0 || sections.keyword.length > 0 || sections.instances.length > 0) && <fieldset className="space-y-3">
      <legend className="text-sm font-medium">文字</legend>
      <div className="grid gap-4 @min-[420px]:grid-cols-2">{sections.text.map(control)}</div>
      {sections.instances.filter(({id}) => values[id] !== undefined).map((instance) => instance.fields ? (
        instance.fields.length > 0 && <details key={instance.id} className="border-t pt-3">
          <summary className="cursor-pointer text-sm">{title(instance.id)}</summary>
          <div className="grid gap-4 pt-3 @min-[420px]:grid-cols-2">{nested(instance)}</div>
        </details>
      ) : nested(instance))}
      {sections.keyword.some((field) => values[field.key] !== undefined) && <div className="space-y-3 border-t pt-3">
        <h4 className="text-sm">关键词</h4>
        <div className="grid gap-4 @min-[420px]:grid-cols-2">{sections.keyword.map(control)}</div>
      </div>}
    </fieldset>}
    <fieldset className="space-y-3">
      <legend className="text-sm font-medium">特效</legend>
      {sections.effects.length ? sections.effects.map(effect) : <p className="text-xs text-muted-foreground">这个字效没有特效。</p>}
    </fieldset>
  </div>;
}

/** 编辑任意 JSON 参数；保留非法中间输入并阻止保存，具体 Schema 约束由服务端检查。 */
function JsonParameter({label, value, disabled, onChange, onValidity}: {
  label: string; value: JsonValue; disabled: boolean;
  onChange: (value: JsonValue) => void; onValidity: (invalid: boolean) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  useEffect(() => {
    setDraft(previous => {
      try { if (sameJson(JSON.parse(previous), value)) return previous; } catch { /* Saved value replaces invalid drafts. */ }
      return JSON.stringify(value, null, 2);
    });
    setError("");
  }, [value]);
  return <div className="space-y-2">
    <Label htmlFor={id}>实例参数 · {label}</Label>
    <Textarea id={id} value={draft} disabled={disabled} aria-invalid={!!error} className="min-h-32 font-mono text-xs"
      onChange={(event) => {
        const text = event.target.value;
        setDraft(text);
        try {
          const next = JSON.parse(text) as JsonValue;
          if (!isJsonValue(next)) throw new Error();
          setError(""); onValidity(false); onChange(next);
        } catch { setError("请输入有效的 JSON 参数。"); onValidity(true); }
      }} />
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </div>;
}
