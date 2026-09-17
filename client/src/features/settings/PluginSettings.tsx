/** 按后端目录生成横向模块导航及设置表单；选择模块后显式保存到客户端本地。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "./api";

/** 一个插件一张表单；只维护编辑值和保存状态，不建立全局配置状态。 */
function PluginForm({ plugin, saved }: { plugin: Plugin; saved?: Values }) {
  const id = useId();
  const [values, setValues] = useState<Values>(() => Object.fromEntries(
    Object.entries(plugin.schema.properties).map(([key, field]) => [key, saved?.[key] ?? field.default ?? ""]),
  ));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  return (
    <form aria-labelledby={id} className="space-y-5 rounded-xl border bg-card p-6" onSubmit={async (event) => {
      event.preventDefault();
      if (saving) return;
      setSaving(true);
      setMessage("");
      setError("");
      try {
        await saveSettings(plugin.id, values);
        setMessage(isTauri() ? "已保存到当前客户端" : "已保存到当前页面，刷新后丢失");
      } catch {
        setError("保存设置失败");
      } finally {
        setSaving(false);
      }
    }}>
      <h3 id={id} className="font-semibold">{plugin.name}</h3>
      <fieldset disabled={saving} className="space-y-4">
        {Object.entries(plugin.schema.properties).map(([key, field]) => (
          <div key={key} className="space-y-2">
            <Label htmlFor={`${id}-${key}`}>{field.title ?? key}</Label>
            <Input
              id={`${id}-${key}`}
              type={field.format === "password" ? "password" : field.type === "boolean" ? "checkbox" : field.type === "string" ? "text" : "number"}
              value={field.type === "boolean" ? undefined : String(values[key] ?? "")}
              checked={field.type === "boolean" ? Boolean(values[key]) : undefined}
              required={plugin.schema.required?.includes(key)}
              min={field.minimum ?? field.exclusiveMinimum}
              max={field.maximum}
              step={field.type === "integer" ? 1 : "any"}
              autoComplete="off"
              onChange={(event) => {
                const text = event.target.value;
                const value = field.type === "boolean" ? event.target.checked
                  : field.type === "string" || text === "" ? text : Number(text);
                setValues((current) => ({ ...current, [key]: value }));
                setMessage("");
              }}
            />
          </div>
        ))}
        <Button type="submit">{saving ? "保存中…" : "保存"}</Button>
      </fieldset>
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </form>
  );
}

/** 默认选择首个模块，切换仅隐藏表单；卸载取消目录读取并忽略迟到结果。 */
export function PluginSettings() {
  const [data, setData] = useState<{ plugins: Plugin[]; values: Record<string, Values> }>();
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([listPlugins(controller.signal), readSettings()]).then(([plugins, values]) => {
      if (!controller.signal.aborted) setData({ plugins, values });
    }).catch(() => {
      if (!controller.signal.aborted) setError("无法加载模块设置，请确认后端服务已启动后重新进入设置页");
    });
    return () => controller.abort();
  }, []);
  const active = selected ?? data?.plugins[0]?.id;
  return (
    <section aria-label="模块设置" className="min-w-0 max-w-3xl space-y-4">
      <p className="text-sm text-muted-foreground">
        {isTauri() ? "配置仅保存到当前客户端，API Key 随配置以明文保存在本地文件中。" : "浏览器预览仅在当前页面保存配置，刷新后丢失。"}
        保存后供后续独立切片请求使用，不修改服务端配置。
      </p>
      {error ? <p role="alert">{error}</p> : !data ? <p role="status">正在读取设置…</p>
        : data.plugins.length === 0 ? <p>暂无配置插件</p>
        : (
          <Tabs value={active} onValueChange={setSelected} className="min-w-0 gap-4">
            <div className="overflow-x-auto pb-1">
              <TabsList aria-label="设置模块" className="gap-1">
                {data.plugins.map((plugin) => (
                  <TabsTrigger key={plugin.id} value={plugin.id} className="flex-none px-4">
                    {plugin.name}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            {data.plugins.map((plugin) => (
              <TabsContent key={plugin.id} value={plugin.id} forceMount hidden={active !== plugin.id}>
                <PluginForm plugin={plugin} saved={data.values[plugin.id]} />
              </TabsContent>
            ))}
          </Tabs>
        )}
    </section>
  );
}
