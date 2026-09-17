/** 根据成功版本 schema 生成按图层分组的表单；控件草稿校验后才影响实时预览。 */
import { cn } from "@/lib/utils";
import { useEffect, useId, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  controlLabel,
  numericRules,
  validValue,
  type Control,
  type Scalar,
  type Values,
  type Version,
} from "./model";

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
  const color = control["x-imv-target"].endsWith("/color");
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
        <Button
          id={id}
          variant="outline"
          aria-pressed={!!value}
          disabled={disabled}
          onClick={() => onChange(!value)}
        >
          {value ? "开启" : "关闭"}
        </Button>
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
            <Input
              type="range"
              aria-label={`${label}滑块`}
              min={rules.min * scale}
              max={rules.max * scale}
              step={rules.step * scale}
              value={
                draft === "" || invalid ? Number(value) * scale : Number(draft)
              }
              disabled={disabled}
              onChange={(event) => edit(event.target.value)}
              onPointerUp={(event) => commit(event.currentTarget.value)}
              onKeyUp={(event) => commit(event.currentTarget.value)}
              onBlur={() => commit()}
              className="h-2 flex-1 border-0 p-0 accent-primary shadow-none"
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
  onChange: (key: string, value: Scalar) => void;
  onSave: () => void;
  onDiscard: () => void;
}) {
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
                ? "正在保存并检查…"
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
            <Button size="sm" disabled={disabled || !dirty} onClick={onSave}>
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
      </div>
      {!version ? (
        <p className="py-7 text-center text-sm text-muted-foreground">
          模板生成后，在这里调整文字、颜色和位置。
        </p>
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
                  control["x-imv-target"].startsWith(`/text_layers/${index}/`),
                )
                .map(([key, control]) => (
                  <div
                    key={key}
                    className={cn(
                      control["x-imv-target"].endsWith("/text") &&
                        "@min-[420px]:col-span-2",
                    )}
                  >
                    <Parameter
                      control={control}
                      value={values[key]}
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
