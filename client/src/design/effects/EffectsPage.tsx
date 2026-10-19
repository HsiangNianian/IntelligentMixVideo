/**
 * AI 字效工作区原型：会话列表（可收起；窗口窄于 1440px 时默认收起，给预览留出空间）| 对话 | 预览 | 参数。
 * 页面持有会话、当前预览的版本、未保存的参数修改与播放状态，并按情况给出锁定原因：生成中、参数未保存、
 * 查看历史版本时不能发送；生成中或查看历史版本时参数只读。
 * 对话栏：新对话时标题、输入框与灵感提示居中显示，发出第一条后输入框滑到底部；最新版本下方给出追问建议；
 * 向上翻看记录时出现「回到底部」按钮。
 * 生成过程在原型里用定时器模拟：按公开阶段依次推进，结束时产生新版本并切到预览；「霓虹描边字幕」会话在后台继续生成。
 * 卸载时清除全部定时器并释放已发送参考图的本地地址。
 */
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { toast } from "sonner";
import { Icon } from "../icons";
import { IconButton } from "../ui";
import { AssistantMessage, FollowUps, UserMessage } from "./Messages";
import { Composer, EmptyHeading, Suggestions } from "./Composer";
import { ParamsPanel } from "./ParamsPanel";
import { PreviewPanel } from "./PreviewPanel";
import { SessionList } from "./SessionList";
import { defaultComposition, initialSessions, stagePlan, type Composition, type Message, type Params, type Session, type Version } from "./model";

/** 新会话没有版本时的初始参数，文字取提示词里「」中的内容。 */
const starter: Params = { text: "新的字效", fontFamily: "思源黑体", fontSize: 88, fontWeight: "700", color: "#FFFFFF", glow: 0.3, underline: 3, enter: 1, y: 50 };
/** 原型按提示词里的颜色词换色，没有提到颜色就保留原色。 */
const colorWords: [RegExp, string][] = [[/金/, "#FFD36B"], [/白/, "#FFFFFF"], [/粉/, "#FF9AD5"], [/紫/, "#B39DFF"], [/蓝|青/, "#4FD1FF"], [/红/, "#FF6B6B"], [/绿/, "#7CFFB2"], [/黄/, "#FFE14D"]];

/** 最新版本下方的追问建议。 */
const followUps = ["字再大一点", "换成白色", "加一个入场动画", "去掉下划线"];

const clock = () => new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
let serial = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(serial += 1)}`;

/** 工作区本体。 */
export function EffectsPage() {
  const [sessions, setSessions] = useState<Session[]>(initialSessions);
  const [currentId, setCurrentId] = useState("s-1");
  const [showSessions, setShowSessions] = useState(() => window.innerWidth >= 1440);
  const [draft, setDraft] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [edits, setEdits] = useState<Params | null>(null);
  const [time, setTime] = useState(2.4);
  const [playing, setPlaying] = useState(true);
  const [loop, setLoop] = useState(true);
  const timers = useRef(new Map<string, number>());
  const blobs = useRef<string[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const timeRef = useRef(time);
  const [focusSignal, setFocusSignal] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  // 定时器回调里需要知道「现在打开的是哪个会话」，用引用避免读到旧值。
  const currentRef = useRef(currentId);
  currentRef.current = currentId;
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;

  const session = sessions.find((item) => item.id === currentId) ?? sessions[0];
  const latest = session.versions.at(-1) ?? null;
  const shown = (previewId && session.versions.find((version) => version.id === previewId)) || latest;
  const history = Boolean(shown && latest && shown.id !== latest.id);
  const params = history ? shown!.params : edits ?? latest?.params ?? null;
  const running = session.messages.some((message) => message.role === "assistant" && message.status === "running");
  const last = session.messages.at(-1);
  const clarifying = last?.role === "assistant" && last.status === "clarify";

  const composerLock = session.status === "deleting" ? "会话正在清理，不能继续发送。"
    : running ? "正在生成，完成或停止后可以继续发送。"
    : edits ? "参数还没保存，保存或撤销后可以继续发送。"
    : history ? "正在查看历史版本，返回最新版本后可以继续发送。"
    : null;
  const paramsLock = running ? "正在生成，完成后可以调整参数。" : history ? "历史版本只读，返回最新版本后可以调整。" : null;

  const update = (id: string, change: (session: Session) => Session) => setSessions((list) => list.map((item) => (item.id === id ? change(item) : item)));
  const updateMessage = (id: string, messageId: string, change: (message: Extract<Message, { role: "assistant" }>) => Message) =>
    update(id, (item) => ({ ...item, messages: item.messages.map((message) => (message.id === messageId && message.role === "assistant" ? change(message) : message)) }));

  // 播放：按真实时间推进，循环开启时回到开头，否则停在结尾；暂停或卸载时停止逐帧计时。
  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    let id = requestAnimationFrame(function tick(now) {
      let next = timeRef.current + (now - previous) / 1000;
      previous = now;
      if (next >= session.composition.seconds) {
        if (!loop) {
          timeRef.current = session.composition.seconds;
          setTime(session.composition.seconds);
          setPlaying(false);
          return;
        }
        next = 0;
      }
      timeRef.current = next;
      setTime(next);
      id = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(id);
  }, [playing, loop, session.composition.seconds]);

  const seek = (next: number) => {
    timeRef.current = next;
    setTime(next);
  };

  /**
   * 模拟生成：从 from 阶段开始，每 1.1 秒推进一个公开阶段，全部完成后产生新版本；
   * 同一会话同时只有一个定时器，停止时清除。
   */
  const generate = (sessionId: string, messageId: string, from: number, prompt: string) => {
    const step = (index: number) => {
      const id = window.setTimeout(() => {
        timers.current.delete(sessionId);
        const elapsed = 2 + Math.round(Math.random() * 7);
        if (index < stagePlan.length) {
          updateMessage(sessionId, messageId, (message) => ({
            ...message,
            stages: [...message.stages.slice(0, -1), { ...message.stages[message.stages.length - 1], seconds: elapsed }, { label: stagePlan[index], seconds: 0 }],
          }));
          step(index + 1);
          return;
        }
        setSessions((list) =>
          list.map((item) => {
            if (item.id !== sessionId) return item;
            const base = item.versions.at(-1)?.params ?? { ...starter, text: /「([^」]+)」/.exec(prompt)?.[1] ?? starter.text };
            const version: Version = {
              id: nextId("v"),
              number: item.versions.length + 1,
              title: base.text,
              source: "agent",
              time: clock(),
              kind: "字效",
              params: {
                ...base,
                color: colorWords.find(([word]) => word.test(prompt))?.[1] ?? base.color,
                glow: /光晕|发光|亮/.test(prompt) ? Math.min(1, Math.round((base.glow + 0.2) * 100) / 100) : base.glow,
              },
            };
            return {
              ...item,
              status: "done",
              versions: [...item.versions, version],
              messages: item.messages.map((message) =>
                message.id === messageId && message.role === "assistant"
                  ? { ...message, status: "done", versionId: version.id, text: `做好了 V${version.number}：按你的描述调整了颜色与光晕，其余参数可以在右侧直接修改。`, stages: [...message.stages.slice(0, -1), { ...message.stages[message.stages.length - 1], seconds: elapsed }] }
                  : message,
              ),
            };
          }),
        );
        if (sessionId === currentRef.current) {
          setPreviewId(null);
          seek(0);
          setPlaying(true);
          toast.success("新版本已生成", { description: "已切到最新版本预览" });
        } else {
          const title = sessionsRef.current.find((item) => item.id === sessionId)?.title;
          toast.success(`「${title}」生成了新版本`, { description: "在会话列表里打开查看" });
        }
      }, 1100);
      timers.current.set(sessionId, id);
    };
    step(from);
  };
  // 「霓虹描边字幕」会话在后台继续生成；卸载时清除定时器并释放参考图地址。
  useEffect(() => {
    generate("s-2", "n-2", 3, "");
    const pending = timers.current;
    const urls = blobs.current;
    return () => {
      pending.forEach((id) => window.clearTimeout(id));
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
    // 只在挂载时启动一次。
  }, []);

  // 新消息或阶段推进时滚到底部。
  const lastStages = last?.role === "assistant" ? last.stages.length : 0;
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [session.messages.length, session.versions.length, lastStages, currentId]);

  const send = (text: string, images: string[]) => {
    blobs.current.push(...images);
    const assistantId = nextId("m");
    update(currentId, (item) => ({
      ...item,
      // 新会话用第一句话作标题：在第一个标点处截断，最多 16 个字。
      title: item.messages.length ? item.title : text.split(/[，。,.！？!?；;\n]/)[0].slice(0, 16) || "参考图字效",
      status: "running",
      messages: [
        ...item.messages,
        { id: nextId("m"), role: "user", text, images },
        { id: assistantId, role: "assistant", text: "", status: "running", stages: [{ label: stagePlan[0], seconds: 0 }] },
      ],
    }));
    setDraft("");
    generate(currentId, assistantId, 1, text);
  };

  const stop = (messageId: string) => {
    window.clearTimeout(timers.current.get(currentId));
    timers.current.delete(currentId);
    updateMessage(currentId, messageId, (message) => ({ ...message, status: "stopped" }));
    update(currentId, (item) => ({ ...item, status: "stopped" }));
  };

  const retry = (messageId: string) => {
    updateMessage(currentId, messageId, (message) => ({ ...message, status: "running", error: undefined, stages: [{ label: stagePlan[0], seconds: 0 }] }));
    update(currentId, (item) => ({ ...item, status: "running" }));
    const prompt = [...session.messages].reverse().find((message) => message.role === "user");
    generate(currentId, messageId, 1, prompt?.role === "user" ? prompt.text : "");
  };

  /** 切换会话：放弃未保存的参数修改并回到最新版本。 */
  const select = (id: string) => {
    if (id === currentId) return;
    if (edits) toast("已放弃未保存的参数修改");
    setEdits(null);
    setPreviewId(null);
    setCurrentId(id);
    seek(0);
  };

  const create = () => {
    const empty = sessions.find((item) => item.messages.length === 0 && item.status !== "deleting");
    if (empty) return select(empty.id);
    const id = nextId("s");
    setSessions((list) => [{ id, title: "新对话", status: "done", group: "今天", composition: defaultComposition, messages: [], versions: [] }, ...list]);
    setEdits(null);
    setPreviewId(null);
    setCurrentId(id);
  };

  const remove = (id: string) => {
    window.clearTimeout(timers.current.get(id));
    const rest = sessions.filter((item) => item.id !== id);
    setSessions(rest);
    toast.success("会话已删除", { description: "已发布到资产的字效不受影响" });
    if (id === currentId) {
      if (rest.length) setCurrentId(rest.find((item) => item.status !== "deleting")?.id ?? rest[0].id);
      else create();
    }
  };

  /** 保存参数修改：成为一个「手动调整」的新版本，并在对话里留一条记录。 */
  const saveParams = () => {
    if (!edits || !latest) return;
    const version: Version = { id: nextId("v"), number: session.versions.length + 1, title: edits.text, source: "params", time: clock(), kind: latest.kind, params: edits };
    update(currentId, (item) => ({
      ...item,
      versions: [...item.versions, version],
      messages: [...item.messages, { id: nextId("m"), role: "assistant", text: `你手动调整了参数，已保存为 V${version.number}。`, status: "done", stages: [], versionId: version.id }],
    }));
    setEdits(null);
    toast.success(`已保存为 V${version.number}`);
  };

  /** 填入输入框并把光标移进去（灵感提示与追问建议共用）。 */
  const fill = (text: string) => {
    setDraft(text);
    setFocusSignal((value) => value + 1);
  };

  const statusText = session.status === "running" ? "正在生成…" : clarifying ? "等待你补充" : session.versions.length ? `${session.versions.length} 个版本` : "还没有版本";

  return (
    <div className="flex h-dvh min-w-0 bg-canvas">
      {showSessions && <SessionList sessions={sessions} currentId={currentId} onSelect={select} onCreate={create} onDelete={remove} />}

      <section aria-label="对话" className="flex w-[400px] shrink-0 flex-col bg-surface xl:w-[440px] 2xl:w-[520px]">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line px-2.5">
          <IconButton label={showSessions ? "收起会话列表" : "展开会话列表"} onClick={() => setShowSessions((value) => !value)}>
            <Icon name={showSessions ? "panel-left-close" : "panel-left-open"} />
          </IconButton>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold leading-tight">{session.title}</p>
            <p className="truncate text-[11px] text-fg-subtle">{statusText}</p>
          </div>
        </header>
        {session.messages.length === 0 ? (
          // 新对话：标题、输入框与灵感提示整体居中；layoutId 让输入框在发出第一条后滑到底部。
          <div className="flex min-h-0 flex-1 flex-col justify-center gap-7 overflow-y-auto px-5 pb-16">
            <EmptyHeading />
            <motion.div layoutId="effects-composer" transition={{ type: "spring", stiffness: 380, damping: 36 }}>
              <Composer
                variant="hero"
                value={draft}
                onValueChange={setDraft}
                lockedReason={composerLock}
                running={running}
                placeholder="例如：暖金色衬线大标题，慢慢淡入，下面一条从中间展开的细线"
                composition={session.composition}
                onCompositionChange={(composition: Composition) => update(currentId, (item) => ({ ...item, composition }))}
                onSend={send}
                onStop={() => last && stop(last.id)}
                focusSignal={focusSignal}
              />
            </motion.div>
            <Suggestions onPick={fill} />
          </div>
        ) : (
          <>
            <div className="relative min-h-0 flex-1">
              <div
                ref={scroller}
                onScroll={(event) => {
                  const element = event.currentTarget;
                  setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight < 80);
                }}
                className="h-full overflow-y-auto"
              >
                <div className="space-y-7 px-5 pb-6 pt-5">
                  {session.messages.map((message) =>
                    message.role === "user" ? (
                      <UserMessage key={message.id} text={message.text} images={message.images} />
                    ) : (
                      <AssistantMessage
                        key={message.id}
                        message={message}
                        version={session.versions.find((version) => version.id === message.versionId)}
                        latest={message.versionId === latest?.id}
                        previewing={Boolean(message.versionId) && message.versionId === shown?.id}
                        onPreview={() => {
                          setPreviewId(message.versionId === latest?.id ? null : message.versionId ?? null);
                          seek(0);
                          setPlaying(true);
                        }}
                        onRetry={() => retry(message.id)}
                        onSaveAsset={(name, kind) => toast.success(`已保存到资产「${name}」`, { description: `分类：${kind} · 可以在模板编辑里添加` })}
                      />
                    ),
                  )}
                  {last?.role === "assistant" && last.versionId && last.versionId === latest?.id && !composerLock && <FollowUps items={followUps} onPick={fill} />}
                </div>
              </div>
              {/* 底部渐隐，让滚动内容在输入框上方柔和地消失。 */}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-linear-to-t from-surface to-transparent" />
              <AnimatePresence>
                {!atBottom && (
                  <motion.button
                    type="button"
                    aria-label="回到底部"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 6 }}
                    onClick={() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" })}
                    className="absolute bottom-3 left-1/2 flex size-8 -translate-x-1/2 items-center justify-center rounded-full border border-line-strong bg-surface text-fg-muted shadow-lift transition-colors hover:text-fg"
                  >
                    <Icon name="alt-arrow-down" className="size-4" />
                  </motion.button>
                )}
              </AnimatePresence>
            </div>
            <motion.div layoutId="effects-composer" transition={{ type: "spring", stiffness: 380, damping: 36 }} className="px-4 pb-4">
              <Composer
                value={draft}
                onValueChange={setDraft}
                lockedReason={composerLock}
                running={running}
                placeholder={clarifying ? "回答上面的问题…" : session.versions.length ? "继续提出修改，比如：字再大一点、颜色换成白色…" : "描述你想要的字效…"}
                composition={session.composition}
                onSend={send}
                onStop={() => last && stop(last.id)}
                focusSignal={focusSignal}
              />
            </motion.div>
          </>
        )}
      </section>

      <PreviewPanel
        version={shown}
        params={params}
        composition={session.composition}
        history={history}
        time={time}
        playing={playing}
        loop={loop}
        onTogglePlay={() => setPlaying((value) => !value)}
        onSeek={(next) => {
          setPlaying(false);
          seek(next);
        }}
        onToggleLoop={() => setLoop((value) => !value)}
        onBackToLatest={() => setPreviewId(null)}
      />
      <ParamsPanel
        version={shown}
        params={params}
        dirty={Boolean(edits)}
        lockedReason={paramsLock}
        onChange={(patch) => latest && setEdits({ ...(edits ?? latest.params), ...patch })}
        onSave={saveParams}
        onRevert={() => setEdits(null)}
      />
    </div>
  );
}
