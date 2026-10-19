/**
 * 原型状态面板：只存在于设计沙盒，用于切换运行环境、列表加载结果、启动页等状态，逐一检查各种情况下的界面。
 * 虚线边框与「原型」字样刻意和产品界面区分，接入正式客户端时整体删除。
 */
import { Popover, ToggleGroup } from "radix-ui";
import { Icon } from "./icons";
import type { ProtoState } from "./mock";

/** 一组互斥选项，渲染为分段按钮。 */
function Segment<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (value: T) => void }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-fg-subtle">{label}</p>
      <ToggleGroup.Root
        type="single"
        value={value}
        onValueChange={(next) => next && onChange(next as T)}
        aria-label={label}
        className="flex rounded-lg bg-sunken p-0.5"
      >
        {options.map(([key, text]) => (
          <ToggleGroup.Item
            key={key}
            value={key}
            className="h-7 flex-1 rounded-md px-2 text-xs text-fg-muted transition-colors hover:text-fg data-[state=on]:bg-raised data-[state=on]:text-fg data-[state=on]:shadow-sm"
          >
            {text}
          </ToggleGroup.Item>
        ))}
      </ToggleGroup.Root>
    </div>
  );
}

/** 面板入口与弹出内容；层级高于启动开场，开场停留等待时也能切换状态。 */
export function PrototypePanel({ value, onChange, onReplaySplash }: { value: ProtoState; onChange: (next: ProtoState) => void; onReplaySplash: () => void }) {
  const set = <K extends keyof ProtoState>(key: K) => (next: ProtoState[K]) => onChange({ ...value, [key]: next });
  return (
    <Popover.Root>
      {/* 入口放在导航栏中段的空白处，不遮挡任何页面内容。 */}
      <Popover.Trigger aria-label="原型状态" title="原型状态" className="fixed bottom-[132px] left-4 z-[90] flex size-10 items-center justify-center rounded-full border border-dashed border-line-strong bg-rail text-fg-subtle transition-colors hover:text-fg data-[state=open]:text-fg">
        <Icon name="test-tube" className="size-4" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="right" align="end" sideOffset={12} className="z-[90] w-72 space-y-3.5 rounded-2xl border border-dashed border-line-strong bg-overlay p-4 shadow-pop data-[state=open]:animate-pop-in">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-fg">切换界面状态 <span className="font-normal text-fg-subtle">· 仅设计稿</span></p>
            <Popover.Close onClick={onReplaySplash} className="flex h-7 items-center gap-1.5 rounded-md bg-raised px-2.5 text-xs text-fg-muted transition-colors hover:bg-hover hover:text-fg">
              <Icon name="play" className="size-3" />重播开场
            </Popover.Close>
          </div>
          <Segment label="配色" value={value.palette} options={[["light", "冷白"], ["graphite", "石墨（深色候选）"]]} onChange={set("palette")} />
          <Segment label="运行环境" value={value.runtime} options={[["desktop", "桌面客户端"], ["browser", "浏览器"]]} onChange={set("runtime")} />
          <Segment label="云端模板" value={value.cloud} options={[["ready", "正常"], ["loading", "加载中"], ["error", "失败"], ["empty", "空"]]} onChange={set("cloud")} />
          <Segment label="本地模板" value={value.local} options={[["ready", "正常"], ["loading", "加载中"], ["empty", "空"]]} onChange={set("local")} />
          <Segment label="启动页" value={value.boot} options={[["off", "关闭"], ["starting", "启动中"], ["failed", "失败"]]} onChange={set("boot")} />
          <Segment label="编辑中的模板" value={value.dirty ? "dirty" : "saved"} options={[["saved", "已保存"], ["dirty", "有未保存修改"]]} onChange={(next) => set("dirty")(next === "dirty")} />
          <Segment label="删除结果" value={value.deleteFails ? "fail" : "ok"} options={[["ok", "成功"], ["fail", "失败"]]} onChange={(next) => set("deleteFails")(next === "fail")} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
