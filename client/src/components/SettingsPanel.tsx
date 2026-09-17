/** 设置面板只展示当前运行环境和连接地址，不修改构建配置或发起网络请求。 */
import { useId } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { apiUrl } from "@/features/remotion_templates/api";

/** 展示实际客户端配置；服务地址仍由现有环境变量管理。 */
export function SettingsPanel() {
  const titleId = useId();
  return (
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
      <p className="mt-4 text-xs leading-6 text-muted-foreground">此处仅展示当前配置。浏览器预览支持云端模板；本地模板库需在桌面客户端中使用。</p>
    </section>
  );
}
