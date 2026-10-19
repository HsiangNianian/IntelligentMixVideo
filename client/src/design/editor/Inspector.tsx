/**
 * 编辑页右栏：没有选中时显示「画面对象」列表（按文字 / 画面 / 转场 / AI 资产分组）；选中后显示该对象的属性面板，
 * 顶部是对象名与关闭按钮。文字对象交给 TextInspector；画面特效与滤镜显示当前效果、更换入口与时间；
 * 转场显示开关、选择方式、统一时长、候选列表与每个连接处实际使用的转场。
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { NumberField, Row, Section, Segmented, Slider, Switch } from "../controls";
import { Icon, type IconName } from "../icons";
import { Button, IconButton } from "../ui";
import { TextInspector, TimingFields } from "./TextInspector";
import {
  footage, joins, objectMeta, spanOf, totalDuration, transitionAt, videos,
  type CategoryId, type EditorObject, type EffectObject, type ObjectKind, type TextObject, type Transitions,
} from "./model";

type Selection = ObjectKind | "transitions" | null;

interface InspectorProps {
  selected: Selection;
  objects: Record<ObjectKind, EditorObject>;
  transitions: Transitions;
  onSelect: (selection: Selection) => void;
  onChangeObject: (kind: ObjectKind, patch: Partial<EditorObject>) => void;
  onChangeTransitions: (patch: Partial<Transitions>) => void;
  onPickEffect: (category: CategoryId) => void;
  onResetText: (kind: ObjectKind) => void;
}

/** 右栏本体。 */
export function Inspector(props: InspectorProps) {
  const { selected, objects, transitions, onSelect } = props;
  return (
    <aside aria-label="属性" className="flex w-[300px] shrink-0 flex-col border-l border-line bg-surface">
      {selected === null ? (
        <ObjectList objects={objects} transitions={transitions} onSelect={onSelect} />
      ) : (
        <>
          <Header
            icon={selected === "transitions" ? "transfer-horizontal" : objectMeta[selected].icon}
            title={selected === "transitions" ? "镜头转场" : objectMeta[selected].label}
            subtitle={selected === "transitions" ? `${transitions.candidates.length} 个候选 · ${transitions.enabled ? "已开启" : "已关闭"}` : describe(objects[selected])}
            onClose={() => onSelect(null)}
          />
          {selected === "transitions" ? (
            <TransitionPanel {...props} />
          ) : "text" in objects[selected] ? (
            <TextInspector
              key={selected}
              object={objects[selected] as TextObject}
              onChange={(patch) => props.onChangeObject(selected, patch)}
              onPickEffect={props.onPickEffect}
              onReset={() => props.onResetText(selected)}
            />
          ) : (
            <EffectPanel key={selected} object={objects[selected] as EffectObject} {...props} />
          )}
        </>
      )}
    </aside>
  );
}

/** 对象的一行说明：效果名与实际区间。 */
function describe(object: EditorObject): string {
  const [start, end] = spanOf(object.timing);
  const effect = object.effect ? (object.kind === "title" || object.kind === "subtitle" ? `花字 ${object.effect.slice(-2)}` : object.kind === "bubble" ? `气泡 ${object.effect.slice(-2)}` : object.effect) : "未设置效果";
  return `${effect} · ${start.toFixed(1)}~${end.toFixed(1)} 秒`;
}

/** 面板顶部：对象图标、名称、说明与关闭按钮。 */
function Header({ icon, title, subtitle, onClose }: { icon: IconName; title: string; subtitle: string; onClose: () => void }) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4">
      <span className="flex size-8 items-center justify-center rounded-lg bg-accent/12 text-accent [&_svg]:size-[18px]">
        <Icon name={icon} variant="duotone" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-semibold leading-tight">{title}</p>
        <p className="truncate text-[11px] text-fg-subtle">{subtitle}</p>
      </div>
      <IconButton label="关闭，回到对象列表" onClick={onClose}>
        <Icon name="close" />
      </IconButton>
    </header>
  );
}

/** 没有选中对象时的列表：按组列出全部对象，点击进入对应面板。 */
function ObjectList({ objects, transitions, onSelect }: Pick<InspectorProps, "objects" | "transitions" | "onSelect">) {
  const groups: { title: string; rows: { key: Selection; icon: IconName; name: string; detail: string; muted?: boolean }[] }[] = [
    { title: "文字", rows: (["title", "subtitle", "bubble"] as const).map((kind) => ({ key: kind, icon: objectMeta[kind].icon, name: objectMeta[kind].label, detail: describe(objects[kind]) })) },
    { title: "画面", rows: (["vfx", "filter"] as const).map((kind) => ({ key: kind, icon: objectMeta[kind].icon, name: objectMeta[kind].label, detail: describe(objects[kind]), muted: !objects[kind].effect })) },
    { title: "转场", rows: [{ key: "transitions", icon: "transfer-horizontal", name: "镜头转场", detail: transitions.enabled ? transitions.candidates.join("、") || "还没有候选" : "已关闭" }] },
  ];
  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
        <p className="text-[13px] font-semibold">画面对象</p>
        <span className="text-[11px] text-fg-subtle">点击对象进行设置</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {groups.map((group) => (
          <div key={group.title} className="mb-2">
            <p className="px-2 pb-1 pt-2 text-[11px] text-fg-subtle">{group.title}</p>
            {group.rows.map((row) => (
              <button key={row.key} type="button" onClick={() => onSelect(row.key)} className="group flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-raised">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-raised text-fg-muted transition-colors group-hover:bg-accent/12 group-hover:text-accent [&_svg]:size-[17px]">
                  <Icon name={row.icon} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-medium">{row.name}</span>
                  <span className={cn("block truncate text-[11px]", row.muted ? "text-fg-subtle" : "text-fg-muted")}>{row.detail}</span>
                </span>
                <Icon name="alt-arrow-down" className="size-3.5 shrink-0 -rotate-90 text-fg-subtle" />
              </button>
            ))}
          </div>
        ))}
        <div className="mx-2 mt-1 rounded-xl border border-dashed border-line-strong px-3 py-3">
          <p className="flex items-center gap-1.5 text-xs font-medium"><Icon name="stars" variant="duotone" className="size-3.5 text-accent" />AI 资产片段</p>
          <p className="mt-1 text-[11px] leading-relaxed text-fg-subtle">在左栏「AI 资产」添加字效、滤镜、视频特效或转场，会叠在预览画面上。</p>
        </div>
      </div>
      <footer className="flex shrink-0 items-center justify-between border-t border-line px-4 py-3 text-[11px] text-fg-subtle">
        <span>画布 1080 × 1920</span>
        <span className="font-mono">{totalDuration.toFixed(1)} 秒 · 3 段视频</span>
      </footer>
    </>
  );
}

/** 画面特效与滤镜：当前效果、更换入口、时间设置与移除。 */
function EffectPanel({ object, onChangeObject, onPickEffect }: { object: EffectObject } & InspectorProps) {
  const label = objectMeta[object.kind].label;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Section title="效果">
        {object.effect ? (
          <div className="flex items-center gap-3 rounded-xl bg-raised p-2">
            <span className="h-12 w-16 shrink-0 rounded-lg" style={{ background: footage[object.effect.length % 3] }} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">{object.effect}</span>
              <span className="block text-[11px] text-fg-subtle">{object.kind === "vfx" ? "画面特效最多一个" : "滤镜可以叠加多个"}</span>
            </span>
            <Button size="sm" onClick={() => onPickEffect(object.kind)}>更换</Button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line-strong px-3 py-5 text-center">
            <p className="text-xs text-fg-muted">还没有选择{label}</p>
            <Button size="sm" onClick={() => onPickEffect(object.kind)}>去左栏选择</Button>
          </div>
        )}
      </Section>
      {object.effect && (
        <>
          <TimingFields timing={object.timing} onChange={(timing) => onChangeObject(object.kind, { timing })} />
          <div className="px-4 pb-4">
            <Button size="sm" variant="ghost" className="w-full text-danger hover:bg-danger/10 hover:text-danger" onClick={() => onChangeObject(object.kind, { effect: null })}>
              <Icon name="trash-bin-minimalistic" />
              移除{label}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

/** 转场面板：关闭时其余设置变灰但保留，预览与合成都不使用转场。 */
function TransitionPanel({ transitions, onChangeTransitions, onPickEffect }: InspectorProps) {
  const { enabled, mode, duration, candidates } = transitions;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Section title="启用转场" action={<Switch label="启用转场" checked={enabled} onChange={(on) => onChangeTransitions({ enabled: on })} />}>
        <p className="text-[11px] leading-relaxed text-fg-subtle">{enabled ? "转场围绕视频连接处居中播放，不改变视频总时长。" : "已关闭：预览与合成都不使用转场，候选列表会保留。"}</p>
      </Section>
      <Dimmed off={!enabled}>
        <Section title="选择方式">
          <Segmented
            label="转场选择方式"
            value={mode}
            onChange={(next) => onChangeTransitions({ mode: next })}
            options={[
              { value: "sequence", label: <><Icon name="sort-from-top-to-bottom" />顺序</> },
              { value: "random", label: <><Icon name="shuffle" />随机</> },
            ]}
          />
          <p className="text-[11px] leading-relaxed text-fg-subtle">{mode === "sequence" ? "按列表顺序依次用在每个连接处，用完从头开始。" : "每个候选最多用一次，候选用完后其余连接处不加转场。"}</p>
        </Section>
        <Section title="时长">
          <Row label="每个转场">
            <Slider label="转场时长" value={duration} min={0.1} max={3} step={0.1} onChange={(next) => onChangeTransitions({ duration: Math.round(next * 10) / 10 })} />
            <NumberField label="转场时长" className="w-[64px] shrink-0" unit="秒" step={0.1} value={duration} min={0.1} max={3} onChange={(next) => onChangeTransitions({ duration: next })} />
          </Row>
        </Section>
        <Section title={`候选转场 · ${candidates.length}`}>
          <div className="space-y-1.5">
            {candidates.map((name, index) => (
              <div key={name} className="flex items-center gap-2.5 rounded-lg bg-raised py-1.5 pl-1.5 pr-1">
                <span className="relative h-7 w-10 shrink-0 overflow-hidden rounded-md" style={{ background: footage[(index + 1) % 3] }}>
                  <span className="absolute inset-0" style={{ background: footage[index % 3], clipPath: "polygon(0 0, 60% 0, 40% 100%, 0 100%)" }} />
                </span>
                <span className="min-w-0 flex-1 truncate text-xs">{name}</span>
                <span className="font-mono text-[10px] text-fg-subtle">#{index + 1}</span>
                <IconButton label={`移除「${name}」`} className="size-7" onClick={() => onChangeTransitions({ candidates: candidates.filter((item) => item !== name) })}>
                  <Icon name="close" />
                </IconButton>
              </div>
            ))}
            <button type="button" onClick={() => onPickEffect("transition")} className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-line-strong text-xs text-fg-muted transition-colors hover:border-accent/50 hover:text-accent [&_svg]:size-4">
              <Icon name="add-circle" />
              从左栏添加转场
            </button>
          </div>
        </Section>
        <Section title="连接处">
          {joins.map((_, index) => {
            const name = transitionAt(transitions, index);
            return (
              <div key={index} className="flex items-center justify-between text-xs">
                <span className="text-fg-muted">{videos[index].label} → {videos[index + 1].label}</span>
                <span className={cn("rounded-full px-2 py-0.5 text-[11px]", name ? "bg-accent/12 text-accent" : "bg-raised text-fg-subtle")}>{name ?? "不使用"}</span>
              </div>
            );
          })}
        </Section>
      </Dimmed>
    </div>
  );
}

/** 关闭时让一组设置变灰且不可操作，但保留内容。 */
function Dimmed({ off, children }: { off: boolean; children: ReactNode }) {
  return <div className={cn("transition-opacity", off && "pointer-events-none opacity-45")} aria-disabled={off || undefined}>{children}</div>;
}
