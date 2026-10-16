/** Remotion 参数控件：按字段类型给出颜色、滑条、下拉、开关、文字（多行字效逐行输入）或关键词输入；非法草稿只留在表单，不进入预览。 */
import { useEffect, useId, useState } from "react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { FontPicker } from "@/features/fonts/FontPicker";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** 一个参数的取值：标量或关键词列表。 */
export type ParameterValue = string | number | boolean | string[];
/** 控件所需的参数描述，由版本 Schema 或已发布资产的参数声明生成。 */
export interface ParameterField {
  key: string;
  label: string;
  kind: "string" | "color" | "number" | "integer" | "boolean" | "keywords";
  options?: (string | number | boolean)[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  maxItems?: number;
  /** 字符串须匹配的 JSON Schema pattern（颜色之外，例如实例内部参数）。 */
  pattern?: string;
  /** 多行字效的文字：固定行数（2～4），值用换行连接各行，每行都不能为空。 */
  lines?: number;
}

/** 颜色参数统一使用 #RRGGBB 或 #RRGGBBAA。 */
export const HEX_COLOR = /^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$/;
/** 关键词输入的分隔符：中英文逗号、顿号与换行。 */
const KEYWORD_SEPARATOR = /[,，、\n]/;
/** 枚举值的中文名称；未列出的取值原样显示。 */
const OPTION_LABELS: Record<string, string> = {
  left: "左对齐",
  center: "居中",
  right: "右对齐",
  "400": "常规",
  "700": "粗体",
  true: "是",
  false: "否",
};

/** 把多行文字的第 index 行改为 value：按 lines 补齐或截断后用换行连接，其余行保持原样。 */
export function withLine(text: string, lines: number, index: number, value: string): string {
  const parts = text.split("\n");
  return Array.from({ length: lines }, (_, line) => (line === index ? value : parts[line] ?? "")).join("\n");
}

/** 按 Unicode 码点计数，与服务端的长度约束一致。 */
function length(text: string): number {
  return Array.from(text).length;
}

/** 字符串是否匹配 JSON Schema pattern；无法编译的 pattern 交给服务端校验。 */
function matches(pattern: string | undefined, value: string): boolean {
  if (pattern === undefined) return true;
  try {
    return new RegExp(pattern, "u").test(value);
  } catch {
    return true;
  }
}

/** 值是否满足字段声明的类型、范围、枚举、长度与项数。 */
export function validParameter(field: ParameterField, value: ParameterValue): boolean {
  if (field.options && !field.options.some((option) => option === value)) return false;
  const fits = (text: string) =>
    length(text) >= (field.minLength ?? 0) && (field.maxLength === undefined || length(text) <= field.maxLength);
  switch (field.kind) {
    case "boolean":
      return typeof value === "boolean";
    case "keywords":
      return Array.isArray(value) && (field.maxItems === undefined || value.length <= field.maxItems) && value.every(fits);
    case "color":
      return typeof value === "string" && HEX_COLOR.test(value);
    case "string":
      return typeof value === "string" && fits(value) && matches(field.pattern, value)
        && ((field.lines ?? 1) <= 1 || value.split("\n").length === field.lines && value.split("\n").every((line) => line.trim() !== ""));
    default:
      return (
        typeof value === "number" &&
        Number.isFinite(value) &&
        (field.kind !== "integer" || Number.isInteger(value)) &&
        (field.minimum === undefined || value >= field.minimum) &&
        (field.maximum === undefined || value <= field.maximum)
      );
  }
}

/** 关键词与输入框文字之间的转换。 */
function keywordsText(value: ParameterValue): string {
  return Array.isArray(value) ? value.join("，") : String(value);
}

/** 把输入框文字解析为字段的值；数字空串保持为空以便提示。 */
function parse(field: ParameterField, text: string): ParameterValue | undefined {
  if (field.kind === "keywords")
    return text.split(KEYWORD_SEPARATOR).map((item) => item.trim()).filter(Boolean);
  if (field.kind === "number" || field.kind === "integer") return text.trim() === "" ? undefined : Number(text);
  return text;
}

/** 单个参数控件；合法输入立即回传，非法中间输入保留并提示，失焦后恢复已生效的值。 */
export function ParameterControl({
  field,
  value,
  disabled,
  onChange,
  className,
}: {
  field: ParameterField;
  value: ParameterValue;
  disabled: boolean;
  onChange: (value: ParameterValue) => void;
  className?: string;
}) {
  const id = useId();
  const numeric = field.kind === "number" || field.kind === "integer";
  const [draft, setDraft] = useState(keywordsText(value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setDraft(keywordsText(value));
    setInvalid(false);
  }, [value]);
  /** 记录草稿；合法且有变化时回传。 */
  function edit(text: string) {
    if (disabled) return;
    setDraft(text);
    const next = parse(field, text);
    const valid = next !== undefined && validParameter(field, next);
    setInvalid(!valid);
    if (valid && JSON.stringify(next) !== JSON.stringify(value)) onChange(next);
  }
  /** 失焦或回车时丢弃仍不合法的草稿。 */
  function settle() {
    const next = parse(field, draft);
    if (next === undefined || !validParameter(field, next)) {
      setDraft(keywordsText(value));
      setInvalid(false);
    }
  }
  const step = field.kind === "integer" ? 1 : field.maximum !== undefined && field.minimum !== undefined && field.maximum - field.minimum <= 4 ? 0.01 : 0.1;
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {field.label}
      </Label>
      {field.key === "fontFamily" ? (
        <FontPicker id={id} label={field.label} value={String(value)} disabled={disabled} onChange={(family) => onChange(family)} />
      ) : field.options ? (
        <Select
          disabled={disabled}
          value={String(value)}
          onValueChange={(text) => {
            const next = field.options!.find((option) => String(option) === text);
            if (next !== undefined) onChange(next);
          }}
        >
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {field.options.map((option) => (
              <SelectItem key={String(option)} value={String(option)}>
                {OPTION_LABELS[String(option)] ?? String(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : field.kind === "boolean" ? (
        <Switch id={id} checked={value === true} disabled={disabled} onCheckedChange={(checked) => onChange(checked)} />
      ) : field.kind === "string" && field.lines && field.lines > 1 ? (
        <div className="space-y-1.5">
          {Array.from({ length: field.lines }, (_, index) => (
            <Input
              key={index}
              id={index === 0 ? id : undefined}
              aria-label={`${field.label}第 ${index + 1} 行`}
              placeholder={`第 ${index + 1} 行`}
              value={draft.split("\n")[index] ?? ""}
              disabled={disabled}
              aria-invalid={invalid}
              onChange={(event) => edit(withLine(draft, field.lines!, index, event.target.value))}
              onBlur={settle}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  settle();
                }
              }}
            />
          ))}
        </div>
      ) : (
        <div className="flex items-center gap-3">
          {field.kind === "color" && (
            <input
              type="color"
              aria-label={`${field.label}选择器`}
              value={String(value).slice(0, 7)}
              disabled={disabled}
              onChange={(event) => edit(event.target.value + (String(value).length === 9 ? String(value).slice(7) : ""))}
              className="size-9 shrink-0 cursor-pointer rounded border bg-transparent p-1"
            />
          )}
          {numeric && field.minimum !== undefined && field.maximum !== undefined && (
            <Slider
              label={`${field.label}滑块`}
              min={field.minimum}
              max={field.maximum}
              step={step}
              value={[invalid || draft === "" ? Number(value) : Number(draft)]}
              disabled={disabled}
              onValueChange={([next]) => edit(String(next))}
              onValueCommit={([next]) => edit(String(next))}
              className="flex-1"
            />
          )}
          <Input
            id={id}
            type={numeric ? "number" : "text"}
            value={draft}
            disabled={disabled}
            aria-invalid={invalid}
            min={field.minimum}
            max={field.maximum}
            step={numeric ? step : undefined}
            placeholder={field.kind === "keywords" ? "多个关键词用逗号分隔" : undefined}
            onChange={(event) => edit(event.target.value)}
            onBlur={settle}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                settle();
              }
            }}
            className={cn(numeric ? "w-24 shrink-0 text-right" : "min-w-0")}
          />
        </div>
      )}
      {invalid && (
        <p className="text-xs text-destructive" role="alert">
          请输入有效的{field.label}
          {field.kind === "keywords" && field.maxItems !== undefined ? `（最多 ${field.maxItems} 个）` : ""}
          {field.lines && field.lines > 1 ? `（${field.lines} 行都要填写）` : ""}。
        </p>
      )}
    </div>
  );
}
