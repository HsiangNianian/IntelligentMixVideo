/**
 * 启动开场：把产品名 IntelligentMixVideo 拆成三段「素材」，在时间线上混剪成片。
 *
 * 分三幕（约 3.5 秒，另有 0.65 秒转场；点击或按任意键跳过）：
 * 1. 入轨（Mix）：时间线标尺展开，Intelligent / Mix / Video 三段素材块从左、上、右飞入并吸附成一条轨道。
 * 2. 渲染（Video、Intelligent）：强调色播放头从左向右扫过，扫过的素材擦除成文字；刚被扫到的字母先滚动随机字符
 *    再锁定（解码），「Mix」锁定时闪一下强调色光。
 * 3. 落版：标尺与播放头退场，标志底板浮现，三条素材从两侧滑入合成播放键，下方是「智能混剪工作台」。
 * 转场：背景淡出露出主页，标志飞到左上角导航栏里同一个标志的位置，完成交接。
 *
 * 播完时内置服务仍在启动（holding）就停在落版画面并显示启动进度，就绪后再转场；
 * 系统要求减少动态效果时直接显示落版画面，只做淡入淡出。
 */
import { useEffect, useRef, useState } from "react";
import { motion, useAnimate, useMotionValue, useReducedMotion, useTransform, type AnimationSequence, type MotionValue } from "motion/react";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";
import { LogoMark } from "./Logo";

/** 三段素材：文字、素材编号；画面色调依次取主题光谱 spectrum-1~3，Mix 与强调色同色。 */
const words = [
  { text: "Intelligent", reel: "A001" },
  { text: "Mix", reel: "A002" },
  { text: "Video", reel: "A003" },
];

/** 素材块的画面：光谱色的斜向渐变，左上角叠一层提亮的高光。 */
function clipFill(index: number): string {
  const color = `var(--color-spectrum-${index + 1})`;
  return `radial-gradient(80% 140% at 18% 0%, color-mix(in oklch, ${color}, white 45%), transparent 60%), linear-gradient(115deg, ${color}, color-mix(in oklch, ${color}, black 40%))`;
}
const letters = words.flatMap((word, index) => [...word.text].map((char) => ({ char, word: index })));
/** 解码时滚动的字符按字宽分组，滚动字符与最终字母宽度相近，不会挤占相邻字母；锁定前每 45ms 换一个。 */
const glyphPools = { narrow: "il1!|:", slim: "tfrj1", wide: "mwMW@%", upper: "ABDEGHKNOPRSUXZ", lower: "abcdeghknopqsuvxyz" };
const decodeMs = 120;

/** 选出与字母宽度相近的滚动字符组。 */
function poolFor(char: string): string {
  if ("ilIj".includes(char)) return glyphPools.narrow;
  if ("tfr".includes(char)) return glyphPools.slim;
  if ("mwMW".includes(char)) return glyphPools.wide;
  return char === char.toUpperCase() ? glyphPools.upper : glyphPools.lower;
}
const expo = [0.16, 1, 0.3, 1] as const;
const inOut = [0.65, 0, 0.35, 1] as const;

/** 每个字母的显示状态：glyph 非空时显示滚动字符，locked 表示已经锁定为正确字母。 */
type LetterState = { glyph: string | null; locked: boolean };

/** 完整时间线；sweep 是播放头位置（0 到 1），同时驱动两层的擦除与字母解码。 */
function introSequence(sweep: MotionValue<number>): AnimationSequence {
  const land = { type: "spring", stiffness: 250, damping: 22 } as const;
  return [
    [".sp-ruler", { opacity: [0, 1], scaleX: [0, 1] }, { duration: 0.6, at: 0.05, ease: expo }],
    [".sp-clip-0", { x: [-170, 0], y: [10, 0], rotate: [-7, 0] }, { ...land, at: 0.2 }],
    [".sp-clip-0", { opacity: [0, 1], filter: ["blur(8px)", "blur(0px)"] }, { duration: 0.35, at: 0.2 }],
    [".sp-clip-1", { y: [-120, 0], rotate: [5, 0], scale: [0.85, 1] }, { ...land, at: 0.3 }],
    [".sp-clip-1", { opacity: [0, 1], filter: ["blur(8px)", "blur(0px)"] }, { duration: 0.35, at: 0.3 }],
    [".sp-clip-2", { x: [170, 0], y: [-10, 0], rotate: [7, 0] }, { ...land, at: 0.4 }],
    [".sp-clip-2", { opacity: [0, 1], filter: ["blur(8px)", "blur(0px)"] }, { duration: 0.35, at: 0.4 }],
    [".sp-hint", { opacity: [0, 1] }, { duration: 0.4, at: 0.6 }],
    // 三段吸附成一条轨道时闪一下。
    [".sp-clips", { filter: ["brightness(1)", "brightness(1.5)", "brightness(1)"] }, { duration: 0.4, at: 1 }],
    [".sp-playhead", { opacity: [0, 1], scaleY: [0, 1] }, { duration: 0.3, at: 1, ease: expo }],
    [sweep, [0, 1], { duration: 1, at: 1.15, ease: inOut }],
    // 标尺与播放头先退场，标志再在腾出的位置合拢。
    [".sp-playhead", { opacity: 0 }, { duration: 0.2, at: 2.15 }],
    [".sp-ruler", { opacity: 0, y: -6 }, { duration: 0.25, at: 2.1 }],
    [".sp-glow", { opacity: [0, 1] }, { duration: 1, at: 1.9 }],
    [".sp-mark-wrap", { opacity: [0, 1] }, { duration: 0.3, at: 2.35 }],
    [".sp-mark .logo-tile", { scale: [0.7, 1] }, { type: "spring", stiffness: 420, damping: 24, at: 2.35 }],
    [".sp-mark .logo-strip-1", { x: [-9, 0], opacity: [0, 1] }, { duration: 0.5, at: 2.45, ease: expo }],
    [".sp-mark .logo-strip-2", { x: [9, 0], opacity: [0, 1] }, { duration: 0.5, at: 2.52, ease: expo }],
    [".sp-mark .logo-strip-3", { x: [-9, 0], opacity: [0, 1] }, { duration: 0.5, at: 2.59, ease: expo }],
    [".sp-tagline", { opacity: [0, 1], y: [8, 0] }, { duration: 0.5, at: 2.5, ease: expo }],
    [".sp-meta", { opacity: [0, 1] }, { duration: 0.5, at: 2.6 }],
    // 落版停留片刻。
    [".sp-meta", { opacity: 1 }, { duration: 0.5, at: 3.1 }],
  ];
}

/** 减少动态效果：直接淡入落版画面，停留后结束。 */
const reducedSequence: AnimationSequence = [
  [".sp-word", { opacity: [0, 1] }, { duration: 0.4 }],
  [".sp-mark-wrap", { opacity: 1 }, { duration: 0.4, at: 0 }],
  [".sp-tagline", { opacity: 1 }, { duration: 0.4, at: 0 }],
  [".sp-meta", { opacity: 1 }, { duration: 0.4, at: 0 }],
  [".sp-glow", { opacity: 1 }, { duration: 0.4, at: 0 }],
  [".sp-meta", { opacity: 1 }, { duration: 1.2, at: 0.4 }],
];

/** 转场：背景淡出；handoff 时标志飞到导航栏标志的位置与大小，否则一起淡出。 */
function exitSequence(root: HTMLElement, handoff: boolean): AnimationSequence {
  const fade: AnimationSequence = [
    [".sp-bg", { opacity: 0 }, { duration: 0.55, ease: "easeInOut" }],
    [".sp-fade", { opacity: 0, filter: "blur(6px)" }, { duration: 0.3, at: 0 }],
  ];
  const mark = root.querySelector(".sp-mark")?.getBoundingClientRect();
  const target = document.querySelector("[data-splash-target] svg")?.getBoundingClientRect();
  if (!handoff || !mark || !target) return [...fade, [".sp-mark", { opacity: 0 }, { duration: 0.3, at: 0 }]];
  const center = (rect: DOMRect) => [rect.left + rect.width / 2, rect.top + rect.height / 2];
  const [fromX, fromY] = center(mark);
  const [toX, toY] = center(target);
  return [...fade, [".sp-mark", { x: toX - fromX, y: toY - fromY, scale: target.width / mark.width }, { duration: 0.65, at: 0, ease: inOut }]];
}

interface SplashProps {
  /** 内置服务仍在启动：落版后停留等待，变为 false 后转场。 */
  holding: boolean;
  /** 转场时把标志交给导航栏；导航栏不可见（例如启动失败）时为 false。 */
  handoff: boolean;
  onDone: () => void;
}

/** 开场本体；动画在字体就绪后开始，卸载时停止全部动画与逐帧计算。 */
export function Splash({ holding, handoff, onDone }: SplashProps) {
  const [scope, animate] = useAnimate<HTMLDivElement>();
  const reduce = useReducedMotion();
  const sweep = useMotionValue(0);
  // 播放头左侧显示文字、右侧显示素材；扫完后取消裁剪，避免锁定时的光晕被切掉。
  const textClip = useTransform(sweep, (value) => (value >= 1 ? "none" : `inset(-60% ${(1 - value) * 100}% -60% -100vw)`));
  const clipsClip = useTransform(sweep, (value) => (value <= 0 ? "none" : `inset(-100vh -100vw -100vh ${value * 100}%)`));
  const playheadLeft = useTransform(sweep, (value) => `${value * 100}%`);
  const [states, setStates] = useState<LetterState[]>(() => letters.map(() => ({ glyph: null, locked: false })));
  const [played, setPlayed] = useState(false);
  const controls = useRef<{ complete: () => void } | null>(null);
  const skipped = useRef(false);
  const stage = useRef<HTMLDivElement>(null);
  const letterRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    let active = true;
    let playback: { stop: () => void } | undefined;
    // 等字体就绪再开始，保证两层的字形宽度一致、逐字解码的位置准确。
    void (document.fonts?.ready ?? Promise.resolve()).then(() => {
      if (!active) return;
      if (reduce) sweep.set(1);
      const run = animate(reduce ? reducedSequence : introSequence(sweep));
      playback = run;
      controls.current = run;
      run.then(() => active && setPlayed(true));
    });
    return () => {
      active = false;
      playback?.stop();
    };
  }, [animate, reduce, sweep]);

  // 逐帧解码：播放头经过字母中线时记下时间，之后 decodeMs 内滚动字符，然后锁定；全部锁定后停止。
  useEffect(() => {
    const passed: number[] = [];
    let previous = "";
    let id = requestAnimationFrame(function tick(now) {
      const box = stage.current?.getBoundingClientRect();
      if (!box) return;
      const head = box.left + box.width * sweep.get();
      const next = letters.map((letter, index): LetterState => {
        const rect = letterRefs.current[index]?.getBoundingClientRect();
        if (passed[index] === undefined && rect && (head >= rect.left + rect.width / 2 || sweep.get() >= 1)) {
          passed[index] = skipped.current || reduce ? -Infinity : now;
        }
        if (passed[index] === undefined) return { glyph: null, locked: false };
        if (now - passed[index] >= decodeMs) return { glyph: null, locked: true };
        const pool = poolFor(letter.char);
        return { glyph: pool[(Math.floor(now / 45) + index * 7) % pool.length], locked: false };
      });
      const key = next.map((state) => (state.locked ? "+" : state.glyph ?? "-")).join("");
      if (key !== previous) {
        previous = key;
        setStates(next);
      }
      if (!next.every((state) => state.locked)) id = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(id);
  }, [sweep, reduce]);

  useEffect(() => {
    if (!played || holding) return;
    let active = true;
    const exit = animate(exitSequence(scope.current, handoff && !reduce));
    exit.then(() => active && onDone());
    return () => {
      active = false;
      exit.stop();
    };
  }, [played, holding, handoff, reduce, animate, scope, onDone]);

  useEffect(() => {
    const skip = () => {
      skipped.current = true;
      controls.current?.complete();
    };
    window.addEventListener("keydown", skip);
    return () => window.removeEventListener("keydown", skip);
  }, []);

  const mixLocked = letters.every((letter, index) => letter.word !== 1 || states[index].locked);
  return (
    <div
      ref={scope}
      role="button"
      tabIndex={-1}
      aria-label="IntelligentMixVideo 开场动画，点击跳过"
      onClick={() => {
        skipped.current = true;
        controls.current?.complete();
      }}
      className="fixed inset-0 z-[80] flex cursor-default select-none items-center justify-center overflow-hidden text-fg outline-none"
    >
      <div className="sp-bg absolute inset-0 bg-splash">
        <div className="grain absolute inset-0 opacity-[0.05]" />
      </div>
      <div className="sp-glow sp-fade pointer-events-none absolute inset-0 bg-[radial-gradient(42%_38%_at_50%_50%,color-mix(in_oklch,var(--color-brand)_12%,transparent),transparent_70%)] opacity-0" />

      <div className="relative flex flex-col items-center">
        <div className="sp-mark-wrap mb-14 opacity-0">
          <LogoMark className="sp-mark size-16 drop-shadow-[0_8px_20px_color-mix(in_oklch,var(--color-brand-to)_35%,transparent)]" />
        </div>

        {/* 舞台：素材层与文字层逐字同构叠放，播放头的位置同时决定两层的擦除边界。 */}
        <div ref={stage} className="sp-word relative text-[clamp(40px,6vw,64px)] font-semibold leading-none tracking-[-0.02em]">
          <Ruler />
          <motion.p aria-hidden="true" className="sp-clips absolute inset-0 flex px-3 text-transparent" style={{ clipPath: clipsClip }}>
            {words.map((word, index) => (
              <span key={word.text} className="relative flex">
                {[...word.text].map((char, offset) => <span key={offset} className="inline-block">{char}</span>)}
                <ClipBlock index={index} reel={word.reel} />
              </span>
            ))}
          </motion.p>
          <motion.p aria-label="IntelligentMixVideo" className="sp-fade relative flex px-3" style={{ clipPath: textClip }}>
            {words.map((word, wordIndex) => (
              <span key={word.text} aria-hidden="true" className={cn("flex", wordIndex === 1 && "text-brand", wordIndex === 1 && mixLocked && "animate-mix-glow")}>
                {letters.map((letter, index) => {
                  if (letter.word !== wordIndex) return null;
                  const state = states[index];
                  return (
                    <span key={index} ref={(element) => { letterRefs.current[index] = element; }} className={cn("relative inline-block", state.locked && "animate-lock-pop")}>
                      <span className={state.glyph ? "invisible" : undefined}>{letter.char}</span>
                      {state.glyph && <span className="absolute inset-0 flex justify-center overflow-x-clip opacity-60">{state.glyph}</span>}
                    </span>
                  );
                })}
              </span>
            ))}
          </motion.p>
          <motion.div className="sp-playhead pointer-events-none absolute -bottom-3 -top-12 origin-top opacity-0" style={{ left: playheadLeft }}>
            <span className="absolute inset-y-0 -left-6 w-12 bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--color-brand)_22%,transparent),transparent)]" />
            <span className="absolute inset-y-0 -left-px w-[2px] rounded-full bg-brand shadow-[0_0_12px_var(--color-brand)]" />
            <svg viewBox="0 0 12 14" className="absolute -left-1.5 -top-0.5 h-3.5 w-3 text-brand" aria-hidden="true">
              <path d="M1 1.5A.5.5 0 0 1 1.5 1h9a.5.5 0 0 1 .5.5V8l-5 5-5-5Z" fill="currentColor" />
            </svg>
          </motion.div>
        </div>

        <p className="sp-tagline sp-fade mt-7 pl-[0.5em] text-sm tracking-[0.5em] text-fg-muted opacity-0">智能混剪工作台</p>
        {/* 启动进度的位置始终保留，出现时不推动上方的落版画面。 */}
        <div className="sp-fade mt-10 h-9" role="status">
          {holding && played && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center gap-3">
              <span className="block h-[3px] w-48 overflow-hidden rounded-full bg-raised">
                <span className="block h-full w-1/3 animate-sweep rounded-full bg-brand" />
              </span>
              <span className="text-xs text-fg-subtle">正在启动内置服务，第一次运行需要初始化数据库…</span>
            </motion.div>
          )}
        </div>
      </div>

      <span className="sp-meta sp-fade pointer-events-none absolute bottom-10 left-1/2 -translate-x-1/2 font-mono text-[11px] tracking-wider text-fg-subtle opacity-0">v{APP_VERSION}</span>
      <span className="sp-hint sp-fade pointer-events-none absolute bottom-10 right-12 text-[11px] text-fg-subtle opacity-0">点击任意处跳过</span>
    </div>
  );
}

/** 时间线标尺：细刻度每 12px 一格、粗刻度每 60px 一格，两端是时间码。 */
function Ruler() {
  return (
    <div className="sp-ruler pointer-events-none absolute -top-12 left-0 right-0 h-6 opacity-0">
      <span className="absolute inset-x-0 bottom-0 h-px bg-line-strong" />
      <span className="absolute inset-x-0 bottom-0 h-1.5 bg-[repeating-linear-gradient(90deg,var(--color-line-strong)_0_1px,transparent_1px_12px)]" />
      <span className="absolute inset-x-0 bottom-0 h-3 bg-[repeating-linear-gradient(90deg,var(--color-fg-subtle)_0_1px,transparent_1px_60px)]" />
      <span className="absolute left-0 top-0 font-mono text-[9px] font-normal leading-none tracking-normal text-fg-subtle">00:00</span>
      <span className="absolute right-0 top-0 font-mono text-[9px] font-normal leading-none tracking-normal text-fg-subtle">00:03</span>
    </div>
  );
}

/** 一段素材块：画面色调、胶片颗粒、帧分隔线、素材编号与音频波形，覆盖对应单词的位置。 */
function ClipBlock({ index, reel }: { index: number; reel: string }) {
  return (
    <span
      className={cn(`sp-clip-${index}`, "absolute -inset-y-[0.1em] inset-x-px overflow-hidden rounded-[10px] opacity-0 shadow-lift ring-1 ring-inset ring-white/20")}
      style={{ background: clipFill(index) }}
    >
      <span className="grain absolute inset-0 opacity-25 mix-blend-overlay" />
      <span className="absolute inset-0 bg-[repeating-linear-gradient(90deg,transparent_0_31px,oklch(0_0_0/0.22)_31px_32px)]" />
      <span className="absolute inset-x-0 top-0 h-px bg-white/35" />
      <span className="absolute left-2 top-1.5 font-mono text-[10px] font-medium leading-none tracking-wider text-white/80">{reel}</span>
      <Waveform seed={index} />
    </span>
  );
}

/** 素材底部的音频波形：按序号生成固定的高低起伏，每次显示一致。 */
function Waveform({ seed }: { seed: number }) {
  const bars = Array.from({ length: 48 }, (_, bar) => 0.25 + 0.75 * Math.abs(Math.sin(bar * 0.9 + seed * 2.1) * Math.cos(bar * 0.37 + seed)));
  return (
    <svg viewBox="0 0 96 10" preserveAspectRatio="none" className="absolute bottom-1.5 left-2 h-2.5 w-[calc(100%-16px)] text-black/35" aria-hidden="true">
      {bars.map((height, bar) => <rect key={bar} x={bar * 2} y={(10 - height * 10) / 2} width="1.1" height={height * 10} fill="currentColor" />)}
    </svg>
  );
}
