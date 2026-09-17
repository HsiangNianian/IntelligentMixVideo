/** 设置页组合环境信息和动态模块配置；配置值保存在当前客户端。 */
import { useId } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { apiUrl } from "@/features/remotion_templates/api";
import { PluginSettings } from "@/features/settings/PluginSettings";

/** 服务地址仍由构建环境决定；模块字段从后端目录生成。 */
export function SettingsPanel() {
  const titleId = useId();
  return (
    <div className="space-y-6">
      <section aria-labelledby={titleId} className="max-w-3xl rounded-xl border bg-card p-6 sm:p-8">
        <h2 id={titleId} className="text-lg font-semibold">环境与连接</h2>
        <p className="mt-2 text-sm text-muted-foreground">查看当前工作台的运行环境与服务地址。</p>
        <dl className="mt-6 divide-y text-sm">
          <div className="flex flex-wrap justify-between gap-3 py-4">
            <dt className="text-muted-foreground">运行环境</dt>
            <dd>{isTauri() ? "桌面客户端" : "浏览器预览"}</dd>
          </div>
          <div className="flex flex-wrap justify-between gap-3 py-4">
            <dt className="text-muted-foreground">字效服务地址</dt>
            <dd className="min-w-0 break-all font-mono text-xs leading-5">{apiUrl("")}</dd>
          </div>
        </dl>
      </section>
      <PluginSettings />
    </div>
  );
}
