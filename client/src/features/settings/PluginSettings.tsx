/** 设置对话框主体：左侧纵向模块导航（首项通用展示环境与连接），右侧滚动表单；选择模块后显式保存到客户端本地。 */
import { useEffect, useId, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Puzzle, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiBase } from "@/lib/api-base";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "./api";
import { normalizeValues, schemaError } from "./schema";

/** 固定面板使用独立值，插件导航统一加前缀，存储 ID 不受影响。 */
const GENERAL_TAB = "general";

/** 导航项共用样式：与首页侧边导航一致，窄屏只显示图标并保留无障碍名称。 */
const navTriggerClass =
  "h-10 w-full flex-none gap-3 rounded-lg px-3 justify-center sm:justify-start hover:bg-muted data-[state=active]:bg-accent data-[state=active]:text-primary group-data-[variant=default]/tabs-list:data-[state=active]:shadow-none";

/** 通用面板展示当前运行环境与服务地址，不依赖后端目录。 */
function GeneralSection() {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="p-5 sm:p-8">
      <h2 id={titleId} className="text-lg font-semibold">环境与连接</h2>
      <p className="mt-2 text-sm text-muted-foreground">查看当前工作台的运行环境与服务地址。</p>
      <dl className="mt-6 divide-y text-sm">
        <div className="flex flex-wrap justify-between gap-3 py-4">
          <dt className="text-muted-foreground">运行环境</dt>
          <dd>{isTauri() ? "桌面客户端" : "浏览器预览"}</dd>
        </div>
        <div className="flex flex-wrap justify-between gap-3 py-4">
          <dt className="text-muted-foreground">后端服务地址</dt>
          <dd className="min-w-0 break-all font-mono text-xs leading-5">{apiBase()}</dd>
        </div>
      </dl>
    </section>
  );
}

/** 一个插件一张表单；只维护编辑值和保存状态，不建立全局配置状态。 */
function PluginForm({ plugin, saved }: { plugin: Plugin; saved?: Values }) {
  const id = useId();
  const [values, setValues] = useState<Values>(() => Object.fromEntries(
    Object.entries(plugin.schema.properties).map(([key, field]) => [key, saved?.[key] ?? field.default ?? (field.type === "boolean" ? false : "")]),
  ));
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; role: "status" | "alert" }>();
  return (
    <form noValidate aria-labelledby={id} className="flex h-full min-h-0 flex-col" onSubmit={async (event) => {
      event.preventDefault();
      if (saving) return;
      setFeedback(undefined);
      // 浏览器可能把未完成的数字（如 1e）暴露为空字符串，不能当成用户清空可选值。
      if (Array.from(event.currentTarget.querySelectorAll<HTMLInputElement>('input[type="number"]')).some(input => input.validity.badInput)) {
        setFeedback({ text: "请输入有效数字", role: "alert" });
        return;
      }
      let normalized: Values;
      try {
        normalized = normalizeValues(plugin, values);
      } catch (reason) {
        setFeedback({ text: (reason as Error).message, role: "alert" });
        return;
      }
      setSaving(true);
      try {
        await saveSettings(plugin.id, normalized);
        setFeedback({ text: isTauri() ? "已保存到当前客户端" : "已保存到当前页面，刷新后丢失", role: "status" });
      } catch {
        setFeedback({ text: "保存设置失败", role: "alert" });
      } finally {
        setSaving(false);
      }
    }}>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8">
      <h3 id={id} className="mb-6 text-lg font-semibold tracking-tight">{plugin.name}</h3>
      <fieldset disabled={saving} className="grid min-w-0 grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
        {Object.entries(plugin.schema.properties).map(([key, field]) => (
          <div key={key} className={field.type === "string" ? "min-w-0 space-y-2 sm:col-span-2" : "min-w-0 space-y-2"}>
            <Label className="text-sm font-medium leading-5" htmlFor={`${id}-${key}`}>{field.title ?? key}</Label>
            <Input
              id={`${id}-${key}`}
              className={field.type === "boolean" ? "size-4 cursor-pointer accent-primary shadow-none" : "h-10 rounded-md border-input/80 bg-background px-3 shadow-xs transition-colors hover:border-ring/50 focus-visible:ring-2 focus-visible:ring-ring/20"}
              type={field.format === "password" ? "password" : field.type === "boolean" ? "checkbox" : field.type === "string" ? "text" : "number"}
              value={field.type === "boolean" ? undefined : String(values[key] ?? "")}
              checked={field.type === "boolean" ? Boolean(values[key]) : undefined}
              required={field.type !== "boolean" && plugin.schema.required?.includes(key)}
              min={field.minimum}
              max={field.maximum}
              step={field.type === "integer" ? 1 : "any"}
              autoComplete="off"
              onChange={(event) => {
                const value = field.type === "boolean" ? event.target.checked : event.target.value;
                setValues((current) => ({ ...current, [key]: value }));
                setFeedback(undefined);
              }}
            />
          </div>
        ))}
      </fieldset>
      </div>
      {/* 操作区独立于字段滚动，长表单仍可随时保存并查看反馈。 */}
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-3 border-t bg-background px-5 py-4 sm:px-8">
        {feedback && <p role={feedback.role} className={feedback.role === "alert"
          ? "min-w-0 flex-1 text-sm text-destructive"
          : "min-w-0 flex-1 text-xs leading-5 text-muted-foreground"}>{feedback.text}</p>}
        <Button type="submit" disabled={saving} className="h-9 min-w-20 rounded-md px-5">{saving ? "保存中…" : "保存"}</Button>
      </div>
    </form>
  );
}

/** 默认选择通用面板，切换仅隐藏表单保留草稿；卸载取消目录读取并忽略迟到结果。 */
export function PluginSettings() {
  const [data, setData] = useState<{ plugins: Plugin[]; values: Record<string, Values> }>();
  const [error, setError] = useState("");
  const [active, setActive] = useState(GENERAL_TAB);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([listPlugins(controller.signal), readSettings()]).then(([plugins, values]) => {
      if (!controller.signal.aborted) setData({ plugins, values });
    }).catch(() => {
      if (!controller.signal.aborted) setError("无法加载模块设置，请确认后端服务已启动后重新打开设置");
    });
    return () => controller.abort();
  }, []);
  return (
    <section aria-label="模块设置" className="flex min-h-0 min-w-0 flex-1 flex-col">
      {error && <p role="alert" className="p-4 sm:p-6">{error}</p>}
      {!data && !error && <p role="status" className="p-4 sm:p-6">正在读取设置…</p>}
          <Tabs orientation="vertical" value={active} onValueChange={setActive} className="min-h-0 flex-1 gap-0">
            <div className="w-14 shrink-0 overflow-y-auto border-r bg-muted/40 p-2 sm:w-52 sm:p-3">
              <TabsList aria-label="设置模块" className="w-full gap-1 rounded-none bg-transparent p-0">
                <TabsTrigger value={GENERAL_TAB} title="通用" className={navTriggerClass}>
                  <Settings2 className="size-[18px]" aria-hidden="true" />
                  <span className="sr-only sm:not-sr-only">通用</span>
                </TabsTrigger>
                {(data?.plugins ?? []).map((plugin) => (
                  <TabsTrigger key={plugin.id} value={`plugin:${plugin.id}`} title={plugin.name} className={navTriggerClass}>
                    <Puzzle className="size-[18px]" aria-hidden="true" />
                    <span className="sr-only sm:not-sr-only">{plugin.name}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            <div className="min-h-0 min-w-0 flex-1">
              <TabsContent className="h-full overflow-y-auto" value={GENERAL_TAB} forceMount hidden={active !== GENERAL_TAB}>
                <GeneralSection />
              </TabsContent>
              {(data?.plugins ?? []).map((plugin) => {
                const unsupported = schemaError(plugin);
                return (
                <TabsContent className="h-full min-h-0" key={plugin.id} value={`plugin:${plugin.id}`} forceMount hidden={active !== `plugin:${plugin.id}`}>
                  {unsupported ? <p role="alert" className="p-5 sm:p-8">{unsupported}</p>
                    : <PluginForm plugin={plugin} saved={data?.values[plugin.id]} />}
                </TabsContent>
                );
              })}
            </div>
          </Tabs>
    </section>
  );
}
