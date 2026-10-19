/**
 * 文字对象（标题、字幕、气泡）的属性面板，分「外观 / 时间 / 关键词」三个页签：字幕不能改时间、没有「时间」页签，
 * 气泡没有「关键词」页签。外观含示例文字、花字（或气泡样式）、字体、字号、位置与动画，循环动画与入场、出场互斥；
 * 关键词含粗斜体等样式与可选的颜色、字号。也导出画面特效与滤镜共用的时间设置 TimingFields。
 */
import { useState } from "react";
import { ToggleGroup } from "radix-ui";
import { cn } from "@/lib/utils";
import { NumberField, Row, Section, Segmented, SelectMenu, Slider, Switch } from "../controls";
import { Icon, type IconName } from "../icons";
import { Button } from "../ui";
import { categories, flowerStyle, fonts, spanOf, totalDuration, type CategoryId, type Keyword, type TextObject, type Timing } from "./model";

interface TextInspectorProps {
  object: TextObject;
  onChange: (patch: Partial<TextObject>) => void;
  /** 在左栏打开对应类目，方便更换花字、气泡或动画。 */
  onPickEffect: (category: CategoryId) => void;
  onReset: () => void;
}

type Tab = "look" | "time" | "keyword";

/** 文字对象面板。切换对象时由父组件按 key 重新挂载，页签回到「外观」。 */
export function TextInspector({ object, onChange, onPickEffect, onReset }: TextInspectorProps) {
  const [tab, setTab] = useState<Tab>("look");
  const tabs: { value: Tab; label: string }[] = [
    { value: "look", label: "外观" },
    ...(object.kind !== "subtitle" ? [{ value: "time" as const, label: "时间" }] : []),
    ...(object.kind !== "bubble" ? [{ value: "keyword" as const, label: "关键词" }] : []),
  ];
  return (
    <>
      <div className="border-b border-line px-4 py-3">
        <Segmented label="设置分类" value={tab} onChange={setTab} options={tabs} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "look" && <LookTab object={object} onChange={onChange} onPickEffect={onPickEffect} onReset={onReset} />}
        {tab === "time" && <TimingFields timing={object.timing} onChange={(timing) => onChange({ timing })} />}
        {tab === "keyword" && <KeywordTab object={object} onChange={(keyword) => onChange({ keyword: { ...object.keyword, ...keyword } })} />}
      </div>
    </>
  );
}

/** 「无」在下拉里用 none 表示，存进草稿时换成 null。 */
const none = "none";
const optionsOf = (category: CategoryId) => [{ value: none, label: "无" }, ...categories.find((item) => item.id === category)!.items.map((item) => ({ value: item.name, label: item.name }))];

/** 外观页签。 */
function LookTab({ object, onChange, onPickEffect, onReset }: TextInspectorProps) {
  const textLabel = { title: "示例标题", subtitle: "示例字幕", bubble: "气泡文字" }[object.kind];
  const looping = object.loop !== null;
  return (
    <>
      <Section title={textLabel}>
        <textarea
          value={object.text}
          onChange={(event) => onChange({ text: event.target.value })}
          rows={2}
          className="w-full resize-none rounded-lg border border-line bg-sunken px-2.5 py-2 text-[13px] leading-relaxed text-fg outline-none transition-[border-color,box-shadow] focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15"
        />
        {object.kind === "subtitle" && <p className="text-[11px] leading-relaxed text-fg-subtle">只用于预览，合成时换成每段切片的原文。</p>}
      </Section>

      <Section title="样式">
        <Row label={object.kind === "bubble" ? "气泡" : "花字"}>
          <button
            type="button"
            onClick={() => onPickEffect(object.kind === "bubble" ? "bubble" : "flower")}
            className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-sunken pl-1 pr-2.5 text-left text-xs transition-colors hover:border-line-strong"
          >
            <span className="flex h-6 w-11 shrink-0 items-center justify-center rounded-md bg-[linear-gradient(160deg,oklch(0.34_0.04_265),oklch(0.16_0.02_270))] text-[11px]" style={object.kind === "bubble" ? { color: "#fff" } : flowerStyle(object.effect)}>
              {object.kind === "bubble" ? "气泡" : "花字"}
            </span>
            <span className={cn("min-w-0 flex-1 truncate", !object.effect && "text-fg-subtle")}>{object.effect ? `${object.kind === "bubble" ? "气泡" : "花字"} ${object.effect.slice(-2)}` : "未设置"}</span>
            <span className="shrink-0 text-[11px] text-accent">更换</span>
          </button>
        </Row>
        <Row label="字体">
          <SelectMenu label="字体" value={object.font} onChange={(font) => onChange({ font })} options={fonts.map((font) => ({ value: font, label: font }))} />
        </Row>
        <Row label="字号">
          <Slider label="字号" value={object.size} min={12} max={300} onChange={(size) => onChange({ size })} />
          <NumberField label="字号" className="w-[60px] shrink-0" value={object.size} min={12} max={300} onChange={(size) => onChange({ size })} />
        </Row>
      </Section>

      <Section title="位置">
        <Row label="中心点">
          <NumberField label="水平位置" prefix="X" unit="%" value={object.x} min={0} max={100} onChange={(x) => onChange({ x })} />
          <NumberField label="垂直位置" prefix="Y" unit="%" value={object.y} min={0} max={100} onChange={(y) => onChange({ y })} />
        </Row>
      </Section>

      <Section title="动画">
        <Row label="入场">
          <SelectMenu label="入场动画" value={object.enter ?? none} onChange={(value) => onChange({ enter: value === none ? null : value, loop: null })} options={optionsOf("enter")} />
          <NumberField label="入场时长" className="w-[60px] shrink-0" unit="s" step={0.1} value={object.enterDuration} min={0.1} max={5} disabled={!object.enter} onChange={(enterDuration) => onChange({ enterDuration })} />
        </Row>
        <Row label="出场">
          <SelectMenu label="出场动画" value={object.exit ?? none} onChange={(value) => onChange({ exit: value === none ? null : value, loop: null })} options={optionsOf("exit")} />
          <NumberField label="出场时长" className="w-[60px] shrink-0" unit="s" step={0.1} value={object.exitDuration} min={0.1} max={5} disabled={!object.exit} onChange={(exitDuration) => onChange({ exitDuration })} />
        </Row>
        <Row label="循环">
          <SelectMenu
            label="循环动画"
            value={object.loop ?? none}
            onChange={(value) => onChange(value === none ? { loop: null } : { loop: value, enter: null, exit: null })}
            options={optionsOf("loop")}
          />
        </Row>
        <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-fg-subtle">
          <Icon name="info-circle" className="mt-px size-3.5 shrink-0" />
          {looping ? "已设置循环动画，入场和出场动画会停用。" : "循环动画与入场、出场动画不能同时使用。"}
        </p>
      </Section>

      <div className="px-4 py-4">
        <Button size="sm" variant="ghost" className="w-full" onClick={onReset}>
          <Icon name="restart" />
          重置文字样式
        </Button>
      </div>
    </>
  );
}

/** 开始方式与持续方式；下方用一条迷你时间轴显示对象在预览里实际占的区间。 */
export function TimingFields({ timing, onChange }: { timing: Timing; onChange: (timing: Timing) => void }) {
  const [start, end] = spanOf(timing);
  return (
    <>
      <Section title="开始">
        <Segmented
          size="sm"
          label="开始方式"
          value={timing.startMode}
          onChange={(startMode) => onChange({ ...timing, startMode, start: 0 })}
          options={[{ value: "seconds", label: "指定秒数" }, { value: "percent", label: "视频进度" }]}
        />
        <Row label="开始于">
          <NumberField
            label="开始时间"
            unit={timing.startMode === "seconds" ? "秒" : "%"}
            step={timing.startMode === "seconds" ? 0.1 : 1}
            value={timing.start}
            min={0}
            max={timing.startMode === "seconds" ? totalDuration : 99}
            onChange={(value) => onChange({ ...timing, start: value })}
          />
        </Row>
      </Section>
      <Section title="持续">
        <Segmented
          size="sm"
          label="持续方式"
          value={timing.duration === null ? "end" : "fixed"}
          onChange={(mode) => onChange({ ...timing, duration: mode === "end" ? null : Math.max(0.1, Math.round((totalDuration - start) * 10) / 10) })}
          options={[{ value: "fixed", label: "固定时长" }, { value: "end", label: "到视频结尾" }]}
        />
        {timing.duration !== null && (
          <Row label="时长">
            <NumberField label="持续时长" unit="秒" step={0.1} value={timing.duration} min={0.1} max={totalDuration} onChange={(duration) => onChange({ ...timing, duration })} />
          </Row>
        )}
      </Section>
      <div className="px-4 py-4">
        <div className="rounded-xl bg-raised p-3">
          <p className="flex items-center justify-between text-xs text-fg-muted">
            <span className="flex items-center gap-1.5"><Icon name="clock-circle" className="size-3.5" />当前预览</span>
            <span className="font-mono text-fg">{start.toFixed(1)} ~ {end.toFixed(1)} 秒</span>
          </p>
          <span className="relative mt-2.5 block h-1.5 rounded-full bg-hover">
            <span className="absolute inset-y-0 rounded-full bg-accent" style={{ left: `${(start / totalDuration) * 100}%`, width: `${((end - start) / totalDuration) * 100}%` }} />
          </span>
          <p className="mt-1.5 flex justify-between font-mono text-[10px] text-fg-subtle"><span>0s</span><span>{totalDuration.toFixed(1)}s</span></p>
        </div>
      </div>
    </>
  );
}

/** 关键词样式开关对应的字段与图标。 */
const styles: { key: "bold" | "italic" | "underline" | "strike"; label: string; icon: IconName }[] = [
  { key: "bold", label: "加粗", icon: "text-bold" },
  { key: "italic", label: "斜体", icon: "text-italic" },
  { key: "underline", label: "下划线", icon: "text-underline" },
  { key: "strike", label: "删除线", icon: "text-cross" },
];
const swatches = ["#FFE14D", "#FF5A5F", "#FF9AD5", "#4FD1FF", "#7CFFB2", "#FFFFFF"];

/** 关键词页签：标题可填预览用的关键词；字幕说明合成时用切片关键词。 */
function KeywordTab({ object, onChange }: { object: TextObject; onChange: (patch: Partial<Keyword>) => void }) {
  const { keyword } = object;
  return (
    <>
      <Section title="关键词">
        {object.kind === "title" ? (
          <>
            <input
              value={keyword.text}
              onChange={(event) => onChange({ text: event.target.value })}
              placeholder="从标题里挑一个词，例如：好物"
              className="h-8 w-full rounded-lg border border-line bg-sunken px-2.5 text-[13px] text-fg outline-none placeholder:text-fg-subtle focus:border-accent/60 focus:ring-[3px] focus:ring-accent/15"
            />
            <p className="text-[11px] leading-relaxed text-fg-subtle">只用于预览，合成时使用文案切片给出的标题关键词。</p>
          </>
        ) : (
          <p className="text-[11px] leading-relaxed text-fg-subtle">合成时每段字幕使用各自切片的关键词；预览里高亮示例字幕的前两个字。</p>
        )}
      </Section>
      <Section title="样式">
        <ToggleGroup.Root
          type="multiple"
          aria-label="关键词样式"
          value={styles.filter((style) => keyword[style.key]).map((style) => style.key)}
          onValueChange={(values) => onChange(Object.fromEntries(styles.map((style) => [style.key, values.includes(style.key)])))}
          className="grid grid-cols-4 gap-1 rounded-lg bg-raised p-0.5"
        >
          {styles.map((style) => (
            <ToggleGroup.Item key={style.key} value={style.key} aria-label={style.label} className="flex h-7 items-center justify-center rounded-md text-fg-muted transition-colors hover:text-fg data-[state=on]:bg-surface data-[state=on]:text-accent data-[state=on]:shadow-[0_1px_2px_oklch(0.2_0.01_265/0.12)] [&_svg]:size-4">
              <Icon name={style.icon} />
            </ToggleGroup.Item>
          ))}
        </ToggleGroup.Root>
      </Section>
      <Section title="颜色" action={<Switch label="设置关键词颜色" checked={keyword.color !== null} onChange={(on) => onChange({ color: on ? "#FFE14D" : null })} />}>
        {keyword.color === null ? (
          <p className="text-[11px] text-fg-subtle">关闭时沿用文字本身的颜色。</p>
        ) : (
          <div className="flex items-center gap-1.5">
            {swatches.map((swatch) => (
              <button
                key={swatch}
                type="button"
                aria-label={`颜色 ${swatch}`}
                onClick={() => onChange({ color: swatch })}
                className={cn("size-6 rounded-full ring-1 ring-black/10 transition-transform hover:scale-110", keyword.color === swatch && "ring-2 ring-accent ring-offset-2 ring-offset-surface")}
                style={{ background: swatch }}
              />
            ))}
            <span className="ml-auto font-mono text-[11px] text-fg-muted">{keyword.color}</span>
          </div>
        )}
      </Section>
      <Section title="字号" action={<Switch label="设置关键词字号" checked={keyword.size !== null} onChange={(on) => onChange({ size: on ? Math.min(300, Math.max(12, object.size)) : null })} />}>
        {keyword.size === null ? (
          <p className="text-[11px] text-fg-subtle">关闭时沿用文字本身的字号。</p>
        ) : (
          <Row label="字号">
            <Slider label="关键词字号" value={keyword.size} min={12} max={300} onChange={(size) => onChange({ size })} />
            <NumberField label="关键词字号" className="w-[60px] shrink-0" value={keyword.size} min={12} max={300} onChange={(size) => onChange({ size })} />
          </Row>
        )}
      </Section>
    </>
  );
}
