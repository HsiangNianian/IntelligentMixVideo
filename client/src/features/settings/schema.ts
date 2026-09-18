/** 设置表单支持的扁平 Schema 子集：渲染前检查描述，保存前校验并规范化值。 */
import type { Plugin, Values } from "./api";

/** 只允许普通对象，避免坏描述进入 Object.entries 或被误渲染为输入框。 */
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 返回不可支持的结构说明；这是表单子集检查，不是完整 JSON Schema 验证器。 */
export function schemaError(plugin: Plugin): string | undefined {
  const schema: unknown = plugin.schema;
  const rootKeys = ["type", "title", "description", "properties", "required", "additionalProperties", "$schema"];
  if (!isObject(schema) || (schema.type !== undefined && schema.type !== "object") || !isObject(schema.properties)
    || Object.keys(schema).some(key => !rootKeys.includes(key))
    || (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== "boolean")) {
    return "暂不支持此模块的配置结构";
  }
  if (schema.required !== undefined && (!Array.isArray(schema.required)
    || schema.required.some(key => typeof key !== "string" || !Object.prototype.hasOwnProperty.call(schema.properties, key)))) {
    return "模块的必填字段描述不合法";
  }
  for (const [key, field] of Object.entries(schema.properties)) {
    const message = `${key}：暂不支持此字段的配置结构或约束`;
    if (!isObject(field) || typeof field.type !== "string" || !["string", "integer", "number", "boolean"].includes(field.type)) return message;
    if (["title", "description"].some(name => field[name] !== undefined && typeof field[name] !== "string")) return message;
    const numeric = field.type === "number" || field.type === "integer";
    const constraints = numeric ? ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"]
      : field.type === "string" ? ["pattern", "minLength", "maxLength", "format"] : [];
    const allowed = ["type", "title", "description", "default", "writeOnly", ...constraints];
    if (Object.keys(field).some(name => !allowed.includes(name))) return message;
    if (field.format !== undefined && field.format !== "password") return message;
    for (const name of constraints.filter(name => name !== "pattern" && name !== "format")) {
      const value = field[name];
      if (value !== undefined && (typeof value !== "number" || !Number.isFinite(value)
        || ((name === "minLength" || name === "maxLength") && (!Number.isInteger(value) || value < 0)))) return message;
    }
    if (field.pattern !== undefined) {
      if (typeof field.pattern !== "string") return message;
      try { new RegExp(field.pattern, "u"); } catch { return message; }
    }
  }
}

/** 从当前 Schema 生成保存快照；省略空的可选数字，拒绝非法值，不修改表单草稿。 */
export function normalizeValues(plugin: Plugin, values: Values): Values {
  const unsupported = schemaError(plugin);
  if (unsupported) throw new Error(unsupported);
  const entries: [string, string | number | boolean][] = [];
  for (const [key, field] of Object.entries(plugin.schema.properties)) {
    const value = values[key];
    const title = field.title ?? key;
    const required = plugin.schema.required?.includes(key);
    if (value === undefined || value === "") {
      if (required) throw new Error(`${title}：必填`);
      if (field.type !== "string" || value === undefined) continue;
    }
    if (field.type === "number" || field.type === "integer") {
      const number = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
      if (!Number.isFinite(number)) throw new Error(`${title}：请输入有效数字`);
      if (field.type === "integer" && !Number.isInteger(number)) throw new Error(`${title}：请输入整数`);
      if (field.minimum !== undefined && number < field.minimum) throw new Error(`${title}：不能小于 ${field.minimum}`);
      if (field.maximum !== undefined && number > field.maximum) throw new Error(`${title}：不能大于 ${field.maximum}`);
      if (field.exclusiveMinimum !== undefined && number <= field.exclusiveMinimum) throw new Error(`${title}：必须大于 ${field.exclusiveMinimum}`);
      if (field.exclusiveMaximum !== undefined && number >= field.exclusiveMaximum) throw new Error(`${title}：必须小于 ${field.exclusiveMaximum}`);
      entries.push([key, number]);
    } else if (field.type === "boolean") {
      if (typeof value !== "boolean") throw new Error(`${title}：请选择开关状态`);
      entries.push([key, value]);
    } else {
      if (typeof value !== "string") throw new Error(`${title}：请输入文本`);
      const length = Array.from(value).length;
      if (field.minLength !== undefined && length < field.minLength) throw new Error(`${title}：至少 ${field.minLength} 个字符`);
      if (field.maxLength !== undefined && length > field.maxLength) throw new Error(`${title}：最多 ${field.maxLength} 个字符`);
      if (field.pattern !== undefined && !new RegExp(field.pattern, "u").test(value)) throw new Error(`${title}：格式不符合要求`);
      entries.push([key, value]);
    }
  }
  return Object.fromEntries(entries);
}
