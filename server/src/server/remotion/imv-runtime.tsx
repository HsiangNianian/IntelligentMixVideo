/**
 * `imv`: the content runtime a free-form text effect (`Effect.tsx`) imports. It carries the content semantics the bus,
 * templates and video composition rely on and no effects at all: how `text`/`textEn` split into lines (and bilingual
 * groups), the first literal hit of each keyword per line, when each line appears (`lineStartFrames`), the per-line
 * font, the stack/overlap layout with `lineGap`/`translationGap`, the six keyword options and the `data-imv-*` markers
 * the host measures. `compose.mjs` inlines this file into the saved Sprite, so published code still imports only
 * React and Remotion; the validation worker resolves it for the TypeScript check.
 */
import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";

/** Props of an effect: the host's content and configuration parameters plus the effect's own style parameters. */
export type EffectProps = { [name: string]: any };

/** One run of a line: plain text or a keyword hit, with its glyphs (grapheme clusters) and their offset in the line. */
export type Run = { text: string; keyword: boolean; glyphs: string[]; start: number };

/**
 * One physical line. `start` is the frame it appears at (`lineStartFrames`), `shown` whether it has appeared, `frame`
 * the frames since it appeared (0 before), `group` its content group (a bilingual pair shares one) and `lang` "en"
 * for the translation line of a bilingual effect. Font, weight and size (with the line's size ratio) are the
 * configuration values for this line.
 */
export type Line = {
  index: number;
  text: string;
  lang: "zh" | "en";
  group: number;
  start: number;
  shown: boolean;
  frame: number;
  fontFamily: string;
  fontWeight: number;
  fontSize: number;
  runs: Run[];
  glyphCount: number;
};

/** Everything `useTextContent` reads from the props, ready to draw. */
export type TextContent = {
  lines: Line[];
  bilingual: boolean;
  overlap: boolean;
  lineGap: number;
  translationGap: number;
  keywords: string[];
  /** The six keyword options as CSS for a hit; empty while they are all off. */
  keywordStyle: React.CSSProperties;
  /** The keywordEffect switch: false means draw hits with the keyword options only, without the effect's own keyword look. */
  keywordEffect: boolean;
  frame: number;
  fps: number;
  duration: number;
};

/** Grapheme clusters of `text` (one unit per visible character), the unit glyph animations count. */
export function splitGlyphs(text: string): string[] {
  const Segmenter = (Intl as any).Segmenter;
  if (typeof Segmenter !== "function") return Array.from(text);
  return Array.from(new Segmenter(undefined, { granularity: "grapheme" }).segment(text), (part: any) => String(part.segment));
}

/** The font stack for a library family, falling back to the bundled CJK sans. */
export function fontStack(family: string): string {
  return `"${family}", "Noto Sans CJK SC", sans-serif`;
}

/** The first literal, non-overlapping hit of each keyword in `line`, as sorted [start, end) character ranges. */
function hits(line: string, keywords: unknown): [number, number][] {
  const found: [number, number][] = [];
  for (const word of Array.isArray(keywords) ? keywords : []) {
    if (typeof word !== "string" || !word) continue;
    for (let at = line.indexOf(word); at >= 0; at = line.indexOf(word, at + 1)) {
      if (!found.some(([start, end]) => at < end && at + word.length > start)) {
        found.push([at, at + word.length]);
        break;
      }
    }
  }
  return found.sort((left, right) => left[0] - right[0]);
}

/** Cut `line` at its keyword hits into runs, numbering glyphs across the whole line. */
function runs(line: string, ranges: [number, number][]): Run[] {
  const parts: { text: string; keyword: boolean }[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) parts.push({ text: line.slice(at, start), keyword: false });
    parts.push({ text: line.slice(start, end), keyword: true });
    at = end;
  }
  if (at < line.length) parts.push({ text: line.slice(at), keyword: false });
  let offset = 0;
  return parts.map((part) => {
    const glyphs = splitGlyphs(part.text);
    const run = { ...part, glyphs, start: offset };
    offset += glyphs.length;
    return run;
  });
}

/** CSS for a keyword hit from the six keyword options; only options that are switched on appear. */
export function keywordStyle(props: EffectProps): React.CSSProperties {
  const decoration = [props.keywordUnderline && "underline", props.keywordStrikeout && "line-through"].filter(Boolean).join(" ");
  return {
    ...(props.keywordBold ? { fontWeight: 700 } : {}),
    ...(props.keywordItalic ? { fontStyle: "italic" } : {}),
    ...(decoration ? { textDecorationLine: decoration } : {}),
    ...(typeof props.keywordColor === "string" && props.keywordColor ? { color: props.keywordColor } : {}),
    ...(typeof props.keywordSize === "number" && props.keywordSize > 0 ? { fontSize: props.keywordSize } : {}),
  };
}

/**
 * Read the effect's content from its props: the physical lines (one; two for 双栏 or one bilingual pair; four for two
 * bilingual groups, Chinese then English), each with its start frame, font and keyword runs. The line count follows
 * `lineStartFrames` and a bilingual effect is one with a `textEn` parameter, exactly as the bus and templates fill them.
 */
export function useTextContent(props: EffectProps): TextContent {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const text = typeof props.text === "string" ? props.text : "";
  const english = typeof props.textEn === "string" ? props.textEn : "";
  const bilingual = typeof props.textEn === "string";
  const starts: unknown[] = Array.isArray(props.lineStartFrames) ? props.lineStartFrames : [];
  const count = Math.max(1, starts.length);
  const paired = bilingual && count === 4;
  const texts = paired
    ? [0, 1].flatMap((i) => [text.split("\n")[i] ?? "", english.split("\n")[i] ?? ""])
    : bilingual
      ? [text, english]
      : count === 1
        ? [text]
        : Array.from({ length: count }, (_, i) => text.split("\n")[i] ?? "");
  const lines = texts.map((value, index): Line => {
    const raw = starts[index];
    const start = typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
    const own = (suffix: string) => props[`line${index + 1}${suffix}`];
    const ratio = typeof own("SizeRatio") === "number" ? own("SizeRatio") : 1;
    const parts = runs(value, hits(value, props.keywords));
    return {
      index,
      text: value,
      lang: bilingual && index % 2 === 1 ? "en" : "zh",
      group: paired ? Math.floor(index / 2) : bilingual ? 0 : index,
      start,
      shown: frame >= start,
      frame: Math.max(0, frame - start),
      fontFamily: String(own("FontFamily") ?? props.fontFamily ?? "Noto Sans CJK SC"),
      fontWeight: Number(own("FontWeight") ?? props.fontWeight ?? 400),
      fontSize: (typeof props.fontSize === "number" ? props.fontSize : 72) * ratio,
      runs: parts,
      glyphCount: parts.reduce((total, run) => total + run.glyphs.length, 0),
    };
  });
  return {
    lines,
    bilingual,
    overlap: lines.length > 1 && props.tierLayout === "overlap",
    lineGap: typeof props.lineGap === "number" ? props.lineGap : 0,
    translationGap: typeof props.translationGap === "number" ? props.translationGap : 8,
    keywords: Array.isArray(props.keywords) ? props.keywords.filter((word: unknown) => typeof word === "string" && word) : [],
    keywordStyle: keywordStyle(props),
    keywordEffect: props.keywordEffect !== false,
    frame,
    fps,
    duration: durationInFrames,
  };
}

/** The wrapper of one line: the host's tier marker, the line's font, its spacing and, before it appears, opacity 0 that keeps its space. */
function LineBox({ line, content, top, children }: { line: Line; content: TextContent; top: number; children: React.ReactNode }) {
  return (
    <div
      data-imv-tier={line.index}
      style={{
        width: "100%",
        gridArea: content.overlap && !(content.bilingual && content.lines.length === 4) ? "1 / 1" : undefined,
        marginTop: top,
        textAlign: "center",
        whiteSpace: "normal",
        wordBreak: "break-all",
        lineHeight: 1.2,
        fontFamily: fontStack(line.fontFamily),
        fontWeight: line.fontWeight,
        fontSize: line.fontSize,
        opacity: line.shown ? 1 : 0,
      }}
    >
      {children}
    </div>
  );
}

/**
 * The required shell of a text effect: it fills the effect's box, stacks the lines in one centred column (or overlaps
 * them when `tierLayout` is overlap), spaces them by `lineGap` (and a bilingual translation by `translationGap`), sets
 * each line's font and hides a line that has not appeared yet while keeping its space. `children` draws one line; an
 * empty line is not drawn. `style` replaces the shell's own layout (alignment, padding) where the effect needs it.
 */
export function TextRoot({ content, style, children }: { content: TextContent; style?: React.CSSProperties; children: (line: Line) => React.ReactNode }) {
  const paired = content.bilingual && content.lines.length === 4;
  const column: React.CSSProperties = content.overlap
    ? { width: "100%", display: "grid", alignItems: "center", justifyItems: "center" }
    : { width: "100%", display: "flex", flexDirection: "column", alignItems: "center" };
  const line = (item: Line, top: number) => item.text ? <LineBox key={item.index} line={item} content={content} top={top}>{children(item)}</LineBox> : null;
  const body = paired
    ? [0, 2].map((first) => {
        const [zh, en] = [content.lines[first], content.lines[first + 1]];
        if (!zh.text && !en.text) return null;
        return (
          <div key={first} data-imv-group={first / 2} style={{ width: "100%", gridArea: content.overlap ? "1 / 1" : undefined, marginTop: first && !content.overlap ? content.lineGap : 0 }}>
            {line(zh, 0)}
            {line(en, content.translationGap)}
          </div>
        );
      })
    : content.lines.map((item) => line(item, item.index === 0 || content.overlap ? 0 : content.lineGap));
  return (
    <div data-imv-text style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", ...style }}>
      <div style={column}>{body}</div>
    </div>
  );
}

/**
 * A keyword hit: the host's keyword marker and the six keyword options over whatever the effect draws for it.
 * Draw the effect's own keyword look (glow, outline, highlight) inside it, and only while `content.keywordEffect` is on.
 */
export function KeywordRun({ content, style, children }: { content: TextContent; run?: Run; style?: React.CSSProperties; children: React.ReactNode }) {
  return <span data-imv-keyword style={{ ...style, ...content.keywordStyle }}>{children}</span>;
}

/** Draw a line as it reads: plain runs as text, keyword hits in `KeywordRun` (or through `keyword` when given). */
export function LineText({ content, line, keyword }: { content: TextContent; line: Line; keyword?: (run: Run, index: number) => React.ReactNode }) {
  return (
    <>
      {line.runs.map((run, index) => run.keyword
        ? <React.Fragment key={index}>{keyword ? keyword(run, index) : <KeywordRun content={content} run={run}>{run.text}</KeywordRun>}</React.Fragment>
        : <React.Fragment key={index}>{run.text}</React.Fragment>)}
    </>
  );
}

/**
 * Timing in the effect's own clock, which the bus sets to the length it renders: `enter` rises 0→1 over `seconds`
 * (after `delay` seconds, from frame `from`, such as a line's start), `exit` falls 1→0 over the last `seconds`.
 */
export function useTiming() {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const frames = (seconds: number) => Math.max(1, Math.round(seconds * fps));
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return {
    frame,
    fps,
    duration: durationInFrames,
    frames,
    enter: (seconds: number, options: { delay?: number; from?: number } = {}) =>
      clamp((frame - (options.from ?? 0) - (options.delay ?? 0) * fps) / frames(seconds)),
    exit: (seconds: number) => clamp((durationInFrames - 1 - frame) / frames(seconds)),
  };
}
