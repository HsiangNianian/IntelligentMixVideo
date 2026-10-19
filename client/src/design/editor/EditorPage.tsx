/**
 * 模板编辑页原型：顶栏（返回、可直接修改的名称与说明、所属库、保存状态与保存）下方是三栏——
 * 左栏素材、中间预览与时间轴、右栏属性。页面持有草稿、选中对象、播放时间与素材面板状态：
 * 在画面、时间轴或对象列表里选中同一个对象，三处联动；点左栏素材即应用到对应对象并选中它。
 * 空格播放 / 暂停，Ctrl（⌘）+ S 保存。原型的「保存」只清除未保存标记，不写入存储。
 */
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Icon } from "../icons";
import { Button, IconButton, PulseDot } from "../ui";
import type { Library } from "../mock";
import { AssetPanel } from "./AssetPanel";
import { Inspector } from "./Inspector";
import { Stage } from "./Stage";
import { Timeline } from "./Timeline";
import {
  categories, initialObjects, initialTransitions, objectMeta, totalDuration,
  type CategoryId, type EditorObject, type EffectItem, type ObjectKind, type TextKind, type TextObject, type Transitions,
} from "./model";

type Selection = ObjectKind | "transitions" | null;

interface EditorPageProps {
  template: { name: string; description: string; library: Library };
  dirty: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onHome: () => void;
}

const textKinds: TextKind[] = ["title", "subtitle", "bubble"];

/** 编辑页本体。 */
export function EditorPage({ template, dirty, onDirtyChange, onHome }: EditorPageProps) {
  const [objects, setObjects] = useState(initialObjects);
  const [transitions, setTransitions] = useState(initialTransitions);
  const [selected, setSelected] = useState<Selection>("title");
  const [time, setTime] = useState(1.2);
  const [playing, setPlaying] = useState(false);
  const [assetTab, setAssetTab] = useState<"ims" | "ai">("ims");
  const [category, setCategory] = useState<CategoryId>("flower");
  const [target, setTarget] = useState<TextKind>("title");
  const [name, setName] = useState(template.name);
  const [description, setDescription] = useState(template.description);
  const timeRef = useRef(time);

  /** 定位播放位置，同时更新逐帧计时用的引用。 */
  const seek = (next: number) => {
    timeRef.current = next;
    setTime(next);
  };

  // 播放：按真实经过的时间推进，到结尾自动停止；暂停或卸载时停止逐帧计时。
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let id = requestAnimationFrame(function tick(now) {
      const next = Math.min(totalDuration, timeRef.current + (now - last) / 1000);
      last = now;
      timeRef.current = next;
      setTime(next);
      if (next >= totalDuration) setPlaying(false);
      else id = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(id);
  }, [playing]);

  const togglePlay = () => {
    if (!playing && timeRef.current >= totalDuration) seek(0);
    setPlaying((value) => !value);
  };

  const save = () => {
    if (!dirty) return;
    onDirtyChange(false);
    toast.success("模板已保存");
  };

  // 空格播放 / 暂停（输入框里除外），Ctrl / ⌘ + S 保存。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && (event.target.isContentEditable || ["INPUT", "TEXTAREA"].includes(event.target.tagName));
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        save();
      } else if (event.code === "Space" && !typing) {
        event.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  /** 选中对象；选中文字对象时，左栏文字类素材的作用对象跟着切换。 */
  const select = (next: Selection) => {
    setSelected(next);
    if (next && textKinds.includes(next as TextKind)) setTarget(next as TextKind);
  };

  const change = (kind: ObjectKind, patch: Partial<EditorObject>) => {
    setObjects((value) => ({ ...value, [kind]: { ...value[kind], ...patch } as EditorObject }));
    onDirtyChange(true);
  };
  const changeTransitions = (patch: Partial<Transitions>) => {
    setTransitions((value) => ({ ...value, ...patch }));
    onDirtyChange(true);
  };

  /** 当前类目实际的作用对象：类目不支持当前对象时取它的第一个可选对象。 */
  const targetOf = (id: CategoryId): TextKind => {
    const targets = categories.find((item) => item.id === id)?.targets;
    return targets && !targets.includes(target) ? targets[0] : target;
  };

  const isApplied = (id: CategoryId, item: EffectItem) => {
    const text = objects[targetOf(id)] as TextObject;
    switch (id) {
      case "flower": return text.effect === item.code;
      case "bubble": return objects.bubble.effect === item.code;
      case "enter": return text.enter === item.name;
      case "exit": return text.exit === item.name;
      case "loop": return text.loop === item.name;
      case "filter": return objects.filter.effect === item.name;
      case "vfx": return objects.vfx.effect === item.name;
      case "transition": return transitions.candidates.includes(item.name);
    }
  };

  /** 应用素材：更新对应对象并选中它；转场追加到候选列表。 */
  const apply = (id: CategoryId, item: EffectItem) => {
    const kind = targetOf(id);
    const patches: Record<CategoryId, () => Selection> = {
      flower: () => (change(kind, { effect: item.code }), kind),
      bubble: () => (change("bubble", { effect: item.code }), "bubble"),
      enter: () => (change(kind, { enter: item.name, loop: null }), kind),
      exit: () => (change(kind, { exit: item.name, loop: null }), kind),
      loop: () => (change(kind, { loop: item.name, enter: null, exit: null }), kind),
      filter: () => (change("filter", { effect: item.name }), "filter"),
      vfx: () => (change("vfx", { effect: item.name }), "vfx"),
      transition: () => {
        if (!transitions.candidates.includes(item.name)) changeTransitions({ candidates: [...transitions.candidates, item.name] });
        return "transitions";
      },
    };
    const next = patches[id]();
    setSelected(next);
    toast(`已应用「${item.name}」`, { description: next === "transitions" ? "加入转场候选" : `作用于${objectMeta[next as ObjectKind].label}` });
  };

  /** 重置文字样式：清除花字与动画，恢复示例文字、字号、位置与动画时长。 */
  const resetText = (kind: ObjectKind) => {
    const origin = initialObjects[kind] as TextObject;
    change(kind, { effect: null, enter: null, exit: null, loop: null, enterDuration: 0.5, exitDuration: 0.5, text: origin.text, size: origin.size, x: origin.x, y: origin.y });
    toast("已重置文字样式");
  };

  return (
    <div className="flex h-dvh min-w-0 flex-col bg-canvas">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-surface pl-2 pr-3">
        <IconButton label="回到主页" onClick={onHome}>
          <Icon name="arrow-left" />
        </IconButton>
        <span className="mx-1 h-5 w-px bg-line" />
        <InlineText label="模板名称" value={name} className="text-[15px] font-semibold" onCommit={(next) => { setName(next); onDirtyChange(true); }} />
        <span className="flex shrink-0 items-center gap-1 rounded-full bg-raised px-2 py-0.5 text-[11px] text-fg-muted">
          <Icon name={template.library === "cloud" ? "cloud" : "laptop-minimalistic"} className="size-3" />
          {template.library === "cloud" ? "云端" : "本地"}
        </span>
        <InlineText label="模板说明" value={description} placeholder="添加说明" allowEmpty className="hidden min-w-0 max-w-[360px] text-xs text-fg-subtle xl:inline-grid" onCommit={(next) => { setDescription(next); onDirtyChange(true); }} />
        <div className="ml-auto flex items-center gap-3">
          <span className={cn("flex items-center gap-1.5 text-xs", dirty ? "text-fg-muted" : "text-fg-subtle")}>
            {dirty ? <PulseDot className="size-1.5 text-warning" /> : <Icon name="check-circle" className="size-3.5 text-success" />}
            {dirty ? "有未保存的修改" : "已保存"}
          </span>
          <Button size="sm" variant="primary" disabled={!dirty} onClick={save}>
            保存
            {dirty && <kbd className="rounded-[5px] border border-on-ink/15 bg-on-ink/10 px-1 font-mono text-[10px] leading-4 text-on-ink/70">Ctrl S</kbd>}
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <AssetPanel
          tab={assetTab}
          onTab={setAssetTab}
          category={category}
          onCategory={setCategory}
          target={target}
          onTarget={setTarget}
          isApplied={isApplied}
          onApply={apply}
          onAddSprite={(spriteName) => toast(`AI 资产「${spriteName}」`, { description: "AI 资产片段的摆放与设置会在后续轮次设计" })}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <Stage
            objects={objects}
            transitions={transitions}
            time={time}
            playing={playing}
            selected={selected}
            onSelect={select}
            onTogglePlay={togglePlay}
            onRestart={() => seek(0)}
            onReplaceVideos={() => toast("预览视频", { description: "更换预览视频的弹窗会在后续轮次设计" })}
          />
          <Timeline objects={objects} transitions={transitions} time={time} selected={selected} onSelect={select} onSeek={seek} />
        </div>
        <Inspector
          selected={selected}
          objects={objects}
          transitions={transitions}
          onSelect={select}
          onChangeObject={change}
          onChangeTransitions={changeTransitions}
          onPickEffect={(id) => {
            setAssetTab("ims");
            setCategory(id);
          }}
          onResetText={resetText}
        />
      </div>
    </div>
  );
}

/**
 * 顶栏里可直接修改的文字：看起来是普通文字，悬停出现底色，点击即可编辑；
 * 宽度随内容变化（隐藏的同内容文本撑开网格）。回车或失焦提交，Esc 放弃；名称不能为空。
 */
function InlineText({ label, value, onCommit, placeholder, allowEmpty, className }: { label: string; value: string; onCommit: (value: string) => void; placeholder?: string; allowEmpty?: boolean; className?: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? value;
  const commit = () => {
    if (draft === null) return;
    const next = draft.trim();
    if ((next || allowEmpty) && next !== value) onCommit(next);
    setDraft(null);
  };
  return (
    <label className={cn("inline-grid min-w-0 rounded-md transition-colors hover:bg-raised focus-within:bg-raised focus-within:ring-[3px] focus-within:ring-accent/15", className)}>
      <span aria-hidden="true" className="invisible col-start-1 row-start-1 truncate whitespace-pre px-1.5 py-0.5">{text || placeholder}</span>
      <input
        aria-label={label}
        value={text}
        placeholder={placeholder}
        maxLength={label === "模板名称" ? 100 : 1000}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(null);
            requestAnimationFrame(() => (event.target as HTMLInputElement).blur());
          }
        }}
        className="col-start-1 row-start-1 w-full min-w-0 truncate bg-transparent px-1.5 py-0.5 outline-none placeholder:text-fg-subtle"
      />
    </label>
  );
}
