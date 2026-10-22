/** 设置对话框主体：普通客户端只展示通用环境与连接，Debug 另按后端目录展示模块表单；显式保存到客户端本地。 */
import { useUpdateBlocker } from "@/lib/update-guard";
import { ClientUpdate } from "@/features/updates/ClientUpdate";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Bug, Puzzle, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiBase, setApiBase } from "@/lib/api-base";
import { listPlugins, readSettings, saveSettings, type Plugin, type Values } from "./api";
import { normalizeValues, schemaError } from "./schema";
import { Spinner } from "@/components/ui/spinner";
import { Hint } from "@/components/Hint";
import { APP_COMMIT, APP_VERSION } from "@/lib/version";
import { openDiagnostics } from "@/lib/diagnostics";

/** 固定面板使用独立值，插件导航统一加前缀，存储 ID 不受影响。 */
const GENERAL_TAB = "general";

/** 导航项共用样式：与首页侧边导航一致，窄屏只显示图标并保留无障碍名称。 */
const navTriggerClass =
  "h-10 w-full flex-none gap-3 rounded-lg px-3 justify-center sm:justify-start hover:bg-muted data-[state=active]:bg-accent data-[state=active]:text-primary group-data-[variant=default]/tabs-list:data-[state=active]:shadow-none";

/** 两种表单共用固定操作栏；取消关闭弹窗，保存当前页成功后关闭。 */
function SettingsActions({ saving, onClose, children }: {
  saving: boolean;
  onClose?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-3 border-t bg-background px-5 py-4 sm:px-8">
      <div className="min-w-0 basis-full text-xs leading-5 text-muted-foreground sm:basis-auto sm:flex-1">
        {children ?? "保存仅作用于当前页"}
      </div>
      <Button type="button" variant="outline" disabled={saving} onClick={onClose} className="h-9 min-w-20 rounded-md px-5">取消</Button>
      <Button type="submit" disabled={saving} className="h-9 min-w-20 rounded-md px-5">{saving ? "保存中…" : "保存"}</Button>
    </div>
  );
}

/** 桌面端按钮打开独立终端窗口，打不开时说明日志文件位置；浏览器不显示，避免用户误以为页面内存在可用的原生日志。 */
function DiagnosticsSection() {
  const [opening, setOpening] = useState(false);
  const [message, setMessage] = useState("");
  if (!isTauri()) return null;
  return <section aria-label="调试日志" className="space-y-3 border-t pt-5 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">调试日志</h3><span className="text-xs text-muted-foreground">排查播放与交互问题</span></div>
    <p className="text-xs leading-5 text-muted-foreground">打开独立终端窗口，实时查看页面错误、媒体信息，以及按钮、页签、表单等控件的操作记录。</p>
    <div className="flex flex-wrap items-center gap-3">
      <Button type="button" variant="outline" size="sm" disabled={opening} onClick={async () => {
        setOpening(true);
        setMessage("");
        try {
          const opened = await openDiagnostics();
          setMessage(opened.viewerError
            ? `${opened.viewerError}。日志仍会写入 ${opened.path}，可用终端跟随查看（如 tail -F）。`
            : "日志窗口已打开，关闭窗口不会退出客户端。");
        } catch (reason) {
          setMessage(reason instanceof Error ? reason.message : String(reason));
        } finally {
          setOpening(false);
        }
      }}><Bug className="size-4" aria-hidden="true" />{opening ? "正在打开…" : "打开调试日志窗口"}</Button>
      {message && <p role={/^无法|失败/.test(message) ? "alert" : "status"} className="text-xs text-muted-foreground">{message}</p>}
    </div>
  </section>;
}

/** 通用地址保存在客户端；不依赖目录，新请求读取保存后的地址。 */
function GeneralSection({ onClose }: { onClose?: () => void }) {
  const titleId = useId();
  const [url, setUrl] = useState(apiBase);
  // 只在地址实际修改时提交：运行时分配的内置后端地址不能被存成固定地址。
  const initial = useRef(apiBase());
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  useUpdateBlocker("设置尚未保存", saving || url.trim() !== initial.current);
  return (
    <section aria-labelledby={titleId} className="flex h-full min-h-0 flex-col">
      <form aria-label="通用设置" className="flex min-h-0 flex-1 flex-col" onSubmit={async (event) => {
        event.preventDefault();
        if (saving) return;
        setSaving(true);
        setMessage("");
        try {
          if (url.trim() !== initial.current) {
            // 宿主按字段合并 $client，其他字段保持磁盘上的最新值。
            await saveSettings("$client", { api_url: url.trim() });
            setApiBase(url.trim());
          }
          initial.current = url.trim();
          setMessage("已保存。后续请求使用新地址；已有会话连接请重启客户端后切换。");
          onClose?.();
        } catch {
          setMessage("保存地址失败，请重试");
        } finally {
          setSaving(false);
        }
      }}>
        <div className="min-h-0 flex-1 overflow-y-auto p-5 sm:p-8">
          <h2 id={titleId} className="text-lg font-semibold">环境与连接</h2>
          <p className="mt-2 text-sm text-muted-foreground">设置当前客户端连接的后端服务地址。</p>
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
          </dl>
          <DiagnosticsSection />
          <ClientUpdate />
        </div>
        <SettingsActions saving={saving} onClose={onClose}>
          {message ? <p role="status">{message}</p> : undefined}
        </SettingsActions>
      </form>
    </section>
  );
}

/** 一个插件一张表单；只维护编辑值和保存状态，不建立全局配置状态。 */
function PluginForm({ plugin, saved, onClose }: { plugin: Plugin; saved?: Values; onClose?: () => void }) {
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
        // 保留当前 Schema 未展示的已存字段；已展示的空可选值仍按规范化结果清除。
        const hidden = Object.fromEntries(Object.entries(saved ?? {}).filter(([key]) => !(key in plugin.schema.properties)));
        await saveSettings(plugin.id, { ...hidden, ...normalized });
        setFeedback({ text: isTauri() ? "已保存到当前客户端" : "已保存到当前页面，刷新后丢失", role: "status" });
        onClose?.();
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
      <SettingsActions saving={saving} onClose={onClose}>
        {feedback ? <p role={feedback.role} className={feedback.role === "alert" ? "text-sm text-destructive" : undefined}>{feedback.text}</p> : undefined}
      </SettingsActions>
    </form>
  );
}

/** 默认选择通用面板，只有 Debug 读取模块；切换保留草稿，卸载取消目录读取并忽略迟到结果。 */
export function PluginSettings({ onClose }: { onClose?: () => void }) {
  const [data, setData] = useState<{ plugins: Plugin[]; values: Record<string, Values> }>();
  const [error, setError] = useState("");
  const [active, setActive] = useState(GENERAL_TAB);
  useEffect(() => {
    if (import.meta.env.IMV_DEBUG !== "true") return;
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
      {import.meta.env.IMV_DEBUG === "true" && !data && !error && <p role="status" className="flex items-center gap-2 p-4 text-sm text-muted-foreground sm:p-6"><Spinner />正在读取设置…</p>}
          <Tabs orientation="vertical" value={active} onValueChange={setActive} className="min-h-0 flex-1 gap-0">
            <div className="flex w-14 shrink-0 flex-col overflow-y-auto border-r bg-muted/40 p-2 sm:w-52 sm:p-3">
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
              {/* 版本号与构建校验码固定在导航栏左下角，窄屏居中显示；校验码可选中复制，用来核对运行的是哪一版代码。 */}
              <p aria-label="应用版本" className="mt-auto pt-3 text-center text-[10px] text-muted-foreground sm:px-3 sm:text-left sm:text-xs">
                v{APP_VERSION}
                <span aria-label="构建校验码" className="block select-all break-all font-mono text-[10px] opacity-80">{APP_COMMIT}</span>
              </p>
            </div>
            <div className="min-h-0 min-w-0 flex-1">
              <TabsContent className="h-full min-h-0" value={GENERAL_TAB} forceMount hidden={active !== GENERAL_TAB}>
                <GeneralSection onClose={onClose} />
              </TabsContent>
              {(data?.plugins ?? []).map((plugin) => {
                const unsupported = schemaError(plugin);
                return (
                <TabsContent className="h-full min-h-0" key={plugin.id} value={`plugin:${plugin.id}`} forceMount hidden={active !== `plugin:${plugin.id}`}>
                  {unsupported ? <p role="alert" className="p-5 sm:p-8">{unsupported}</p>
                    : <PluginForm plugin={plugin} saved={data?.values[plugin.id]} onClose={onClose} />}
                </TabsContent>
                );
              })}
            </div>
          </Tabs>
    </section>
  );
}
