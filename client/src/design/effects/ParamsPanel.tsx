/**
 * AI 字效的参数面板：按分组列出当前版本的参数（文字、字体、字重、字号、颜色、光晕、下划线、淡入、纵向位置），
 * 修改即时反映到预览；底部固定「撤销 / 保存修改」，保存后成为一个「手动调整」的新版本。
 * 生成进行中或查看历史版本时整块只读，并在顶部说明原因。
 */
import type { ReactNode } from "react";
import { Popover } from "radix-ui";
import { cn } from "@/lib/utils";
import { NumberField, Row, Section, Segmented, SelectMenu, Slider } from "../controls";
import { Icon } from "../icons";
import { Button, PulseDot } from "../ui";
import { fontLibrary, paramGroups, type ParamControl, type Params, type Version } from "./model";

interface ParamsPanelProps {
  version: Version | null;
  params: Params | null;
  dirty: boolean;
  /** 非空时面板只读，内容为原因。 */
  lockedReason: string | null;
  onChange: (patch: Partial<Params>) => void;
  onSave: () => void;
  onRevert: () => void;
}

/** 参数面板本体。 */
export function ParamsPanel({ version, params, dirty, lockedReason, onChange, onSave, onRevert }: ParamsPanelProps) {
  return (
    <aside aria-label="参数" className="flex w-[288px] shrink-0 flex-col border-l border-line bg-surface">
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-line px-4">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold leading-tight">参数</p>
          <p className="truncate text-[11px] text-fg-subtle">{version ? `V${version.number} · ${version.title}` : "生成后可以调整"}</p>
        </div>
        {version && (
          <span className={cn("flex shrink-0 items-center gap-1.5 text-[11px]", dirty ? "text-fg-muted" : "text-fg-subtle")}>
            {dirty ? <PulseDot className="size-1.5 text-warning" /> : <Icon name="check-circle" className="size-3.5 text-success" />}
            {dirty ? "未保存" : "已保存"}
          </span>
        )}
      </header>

      {!params ? (
        <p className="px-4 py-10 text-center text-xs leading-relaxed text-fg-subtle">生成第一个版本后，字号、颜色、动画等参数会出现在这里，改动会实时显示在预览里。</p>
      ) : (
        <>
          {lockedReason && (
            <p className="flex items-start gap-1.5 border-b border-line bg-raised/60 px-4 py-2.5 text-[11.5px] leading-relaxed text-fg-muted">
              <Icon name="lock-keyhole-minimalistic" className="mt-px size-3.5 shrink-0" />
              {lockedReason}
            </p>
          )}
          <fieldset disabled={Boolean(lockedReason)} className={cn("min-h-0 flex-1 overflow-y-auto transition-opacity", lockedReason && "pointer-events-none opacity-60")}>
            {paramGroups.map((group) => (
              <Section key={group.title} title={group.title}>
                {group.controls.map((control) => (
                  <Control key={control.key} control={control} params={params} onChange={onChange} />
                ))}
              </Section>
            ))}
          </fieldset>
          <footer className="flex shrink-0 gap-2 border-t border-line p-3">
            <Button size="sm" variant="ghost" className="flex-1" disabled={!dirty || Boolean(lockedReason)} onClick={onRevert}>
              <Icon name="restart" />
              撤销
            </Button>
            <Button size="sm" variant="primary" className="flex-[1.6]" disabled={!dirty || Boolean(lockedReason)} onClick={onSave}>保存为新版本</Button>
          </footer>
        </>
      )}
    </aside>
  );
}

/** 按控件类型画出一行参数。 */
function Control({ control, params, onChange }: { control: ParamControl; params: Params; onChange: (patch: Partial<Params>) => void }) {
  switch (control.type) {
    case "text":
      return (
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-fg-muted">{control.label}</span>
            <span className="font-mono text-[10.5px] text-fg-subtle">{params.text.length}/{control.max}</span>
          </div>
          <input
            value={params.text}
            maxLength={control.max}
            onChange={(event) => onChange({ text: event.target.value })}
            className="h-8 w-full rounded-lg border border-line bg-sunken px-2.5 text-[13px] text-fg outline-none focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15"
          />
        </div>
      );
    case "font":
      return (
        <Row label={control.label}>
          <SelectMenu
            label="字体"
            value={params.fontFamily}
            onChange={(fontFamily) => onChange({ fontFamily })}
            options={fontLibrary.map((font) => ({ value: font, label: font }))}
            renderOption={(option) => <span style={{ fontFamily: option.value.includes("宋") ? "var(--font-serif)" : undefined }}>{option.label}</span>}
          />
        </Row>
      );
    case "segmented":
      return (
        <Row label={control.label}>
          <Segmented size="sm" className="flex-1" label={control.label} value={params.fontWeight} onChange={(fontWeight) => onChange({ fontWeight })} options={control.options} />
        </Row>
      );
    case "range": {
      const value = params[control.key];
      return (
        <Row label={control.label}>
          <Slider label={control.label} value={value} min={control.min} max={control.max} step={control.step} onChange={(next) => onChange({ [control.key]: next })} />
          <NumberField label={control.label} className="w-[64px] shrink-0" unit={control.unit} value={value} min={control.min} max={control.max} step={control.step} onChange={(next) => onChange({ [control.key]: next })} />
        </Row>
      );
    }
    case "color":
      return (
        <Row label={control.label}>
          <ColorPicker value={params.color} onChange={(color) => onChange({ color })} />
        </Row>
      );
  }
}

const palette = ["#F6D58E", "#FFFFFF", "#FFE14D", "#FF8A65", "#FF9AD5", "#B39DFF", "#4FD1FF", "#7CFFB2"];

/** 颜色：色块加十六进制值，点击弹出常用色板与输入框。 */
function ColorPicker({ value, onChange }: { value: string; onChange: (value: string) => void }): ReactNode {
  return (
    <Popover.Root>
      <Popover.Trigger className="flex h-8 w-full items-center gap-2 rounded-lg border border-line bg-sunken px-1.5 text-left transition-colors hover:border-line-strong disabled:pointer-events-none data-[state=open]:border-accent/60">
        <span className="size-5 rounded-md ring-1 ring-black/10" style={{ background: value }} />
        <span className="font-mono text-xs text-fg">{value.toUpperCase()}</span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={6} className="z-[70] w-56 space-y-3 rounded-xl border border-line-strong bg-overlay p-3 shadow-pop data-[state=open]:animate-pop-in">
          <div className="grid grid-cols-8 gap-1.5">
            {palette.map((color) => (
              <button key={color} type="button" aria-label={color} onClick={() => onChange(color)} className={cn("size-5 rounded-md ring-1 ring-black/10 transition-transform hover:scale-110", color === value.toUpperCase() && "ring-2 ring-accent ring-offset-1 ring-offset-overlay")} style={{ background: color }} />
            ))}
          </div>
          <input
            defaultValue={value}
            onBlur={(event) => /^#[0-9a-f]{6}$/i.test(event.target.value) && onChange(event.target.value.toUpperCase())}
            className="h-8 w-full rounded-lg border border-line bg-sunken px-2 font-mono text-xs text-fg outline-none focus:border-accent/60"
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
