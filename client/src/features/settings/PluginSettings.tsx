/** 设置对话框主体：左侧纵向模块导航（首项通用展示环境与连接），右侧滚动表单；选择模块后显式保存到客户端本地。 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Puzzle, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiBase, setApiBase } from "@/lib/api-base";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "./api";
import { normalizeValues, schemaError } from "./schema";
import { Spinner } from "@/components/ui/spinner";
import { Hint } from "@/components/Hint";

/** 固定面板使用独立值，插件导航统一加前缀，存储 ID 不受影响。 */
const GENERAL_TAB = "general";

/** 导航项共用样式：与首页侧边导航一致，窄屏只显示图标并保留无障碍名称。 */
const navTriggerClass =
  "h-10 w-full flex-none gap-3 rounded-lg px-3 justify-center sm:justify-start hover:bg-muted data-[state=active]:bg-accent data-[state=active]:text-primary group-data-[variant=default]/tabs-list:data-[state=active]:shadow-none";

/** 两种表单共用固定操作栏；取消关闭弹窗，保存只提交当前页。 */
function SettingsActions({ saving, onCancel, children }: {
  saving: boolean;
  onCancel?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-3 border-t bg-background px-5 py-4 sm:px-8">
      <div className="min-w-0 basis-full text-xs leading-5 text-muted-foreground sm:basis-auto sm:flex-1">
        {children ?? "保存仅作用于当前页"}
      </div>
      <Button type="button" variant="outline" disabled={saving} onClick={onCancel} className="h-9 min-w-20 rounded-md px-5">取消</Button>
      <Button type="submit" disabled={saving} className="h-9 min-w-20 rounded-md px-5">{saving ? "保存中…" : "保存"}</Button>
    </div>
  );
}

/** 通用地址与本地模板路径保存在客户端；不依赖目录，新请求读取保存后的地址。 */
function GeneralSection({ onCancel }: { onCancel?: () => void }) {
  const titleId = useId();
  const [url, setUrl] = useState(apiBase);
  // 读取到已存值前禁用路径输入，避免用空值覆盖已保存路径。
  const [path, setPath] = useState<string>();
  // 只提交相对打开时实际修改的字段：运行时分配的内置后端地址不能被存成固定地址，也不改回其他实例保存的路径。
  const initial = useRef({ url: apiBase(), path: "" });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    if (isTauri()) readSettings().then(saved => {
      if (!active) return;
      initial.current.path = String(saved.$client?.template_path ?? "");
      setPath(initial.current.path);
    }, () => undefined);
    return () => { active = false; };
  }, []);
  return (
    <section aria-labelledby={titleId} className="flex h-full min-h-0 flex-col">
      <form aria-label="通用设置" className="flex min-h-0 flex-1 flex-col" onSubmit={async (event) => {
        event.preventDefault();
        if (saving) return;
        setSaving(true);
        setMessage("");
        try {
          const values: Values = {};
          if (url.trim() !== initial.current.url) values.api_url = url.trim();
          if (path !== undefined && path.trim() !== initial.current.path) values.template_path = path.trim();
          // 宿主按字段合并 $client，未修改的字段保持磁盘上的最新值。
          if (Object.keys(values).length) await saveSettings("$client", values);
          if (values.api_url !== undefined) setApiBase(url.trim());
          initial.current = { url: url.trim(), path: path?.trim() ?? initial.current.path };
          setMessage("已保存。后续请求使用新地址；本地模板读写立即使用新路径，已打开的本地模板需回主页重新打开；已有会话连接请重启客户端后切换。");
        } catch {
          setMessage("保存地址失败，请重试");
        } finally {
          setSaving(false);
        }
      }}>
        <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-8">
          <h2 id={titleId} className="text-lg font-semibold">环境与连接</h2>
          <p className="mt-2 text-sm text-muted-foreground">设置当前客户端连接的后端服务地址和本地模板保存位置。</p>
          <dl className="mt-6 divide-y text-sm">
            <div className="flex flex-wrap justify-between gap-3 py-4">
              <dt className="text-muted-foreground">运行环境</dt>
              <dd>{isTauri() ? "桌面客户端" : "浏览器预览"}</dd>
            </div>
            <div className="flex flex-wrap justify-between gap-3 py-4">
              <dt className="text-muted-foreground"><Label htmlFor={`${titleId}-url`}>后端服务地址</Label></dt>
              <dd className="w-full min-w-0">
                <Input id={`${titleId}-url`} type="url" required pattern="https?://.+" value={url}
                  onChange={event => { setUrl(event.target.value); setMessage(""); }} disabled={saving} />
              </dd>
            </div>
            <div className="flex flex-wrap justify-between gap-3 py-4">
              <dt className="text-muted-foreground"><Label htmlFor={`${titleId}-path`}>本地模板保存路径</Label></dt>
              <dd className="w-full min-w-0 space-y-2">
                <Input id={`${titleId}-path`} value={path ?? ""} disabled={path === undefined || saving}
                  placeholder="留空使用默认 data/template/templates.json"
                  onChange={event => { setPath(event.target.value); setMessage(""); }} />
                <p className="text-xs text-muted-foreground">{isTauri() ? "填写 .json 绝对文件路径，留空恢复默认；原文件保留，不自动迁移。" : "本地模板仅支持桌面客户端。"}</p>
              </dd>
            </div>
          </dl>
        </div>
        <SettingsActions saving={saving} onCancel={onCancel}>
          {message ? <p role="status">{message}</p> : undefined}
        </SettingsActions>
      </form>
    </section>
  );
}

/** 一个插件一张表单；只维护编辑值和保存状态，不建立全局配置状态。 */
function PluginForm({ plugin, saved, onCancel }: { plugin: Plugin; saved?: Values; onCancel?: () => void }) {
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
        // 普通模式保存时保留未展示的 Debug 字段；已展示的空可选值仍按规范化结果清除。
        const hidden = Object.fromEntries(Object.entries(saved ?? {}).filter(([key]) => !(key in plugin.schema.properties)));
        await saveSettings(plugin.id, { ...hidden, ...normalized });
        setFeedback({ text: isTauri() ? "已保存到当前客户端" : "已保存到当前页面，刷新后丢失", role: "status" });
      } catch {
        setFeedback({ text: "保存设置失败", role: "alert" });
      } finally {
        setSaving(false);
      }
    }}>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8">
      <h3 id={id} className="mb-6 text-lg font-semibold tracking-tight">{plugin.name}</h3>
      {plugin.description && <p className="mb-5 text-xs text-muted-foreground">{plugin.description}</p>}
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
      <SettingsActions saving={saving} onCancel={onCancel}>
        {feedback ? <p role={feedback.role} className={feedback.role === "alert" ? "text-sm text-destructive" : undefined}>{feedback.text}</p> : undefined}
      </SettingsActions>
    </form>
  );
}

/** 默认选择通用面板，切换仅隐藏表单保留草稿；卸载取消目录读取并忽略迟到结果。 */
export function PluginSettings({ onCancel }: { onCancel?: () => void }) {
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
      {!data && !error && <p role="status" className="flex items-center gap-2 p-4 text-sm text-muted-foreground sm:p-6"><Spinner />正在读取设置…</p>}
          <Tabs orientation="vertical" value={active} onValueChange={setActive} className="min-h-0 flex-1 gap-0">
            <div className="w-14 shrink-0 overflow-y-auto border-r bg-muted/40 p-2 sm:w-52 sm:p-3">
              <TabsList aria-label="设置模块" className="w-full gap-1 rounded-none bg-transparent p-0">
                <Hint label="通用" side="right" visibleBelow="sm"><div className="flex w-full"><TabsTrigger value={GENERAL_TAB} className={navTriggerClass}>
                  <Settings2 className="size-[18px]" aria-hidden="true" />
                  <span className="sr-only sm:not-sr-only">通用</span>
                </TabsTrigger></div></Hint>
                {(data?.plugins ?? []).map((plugin) => (
                  <Hint key={plugin.id} label={plugin.name} side="right" visibleBelow="sm"><div className="flex w-full"><TabsTrigger value={`plugin:${plugin.id}`} className={navTriggerClass}>
                    <Puzzle className="size-[18px]" aria-hidden="true" />
                    <span className="sr-only sm:not-sr-only">{plugin.name}</span>
                  </TabsTrigger></div></Hint>
                ))}
              </TabsList>
            </div>
            <div className="min-h-0 min-w-0 flex-1">
              <TabsContent className="h-full min-h-0" value={GENERAL_TAB} forceMount hidden={active !== GENERAL_TAB}>
                <GeneralSection onCancel={onCancel} />
              </TabsContent>
              {(data?.plugins ?? []).map((plugin) => {
                const unsupported = schemaError(plugin);
                return (
                <TabsContent className="h-full min-h-0" key={plugin.id} value={`plugin:${plugin.id}`} forceMount hidden={active !== `plugin:${plugin.id}`}>
                  {unsupported ? <p role="alert" className="p-5 sm:p-8">{unsupported}</p>
                    : <PluginForm plugin={plugin} saved={data?.values[plugin.id]} onCancel={onCancel} />}
                </TabsContent>
                );
              })}
            </div>
          </Tabs>
    </section>
  );
}
