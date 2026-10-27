/**
 * Bundle Preset TSX modules into one Sprite module without executing generated code.
 * The Agent times instances against the length the user previewed; the generated host lays them out against the
 * length it is actually rendered at (`useVideoConfig().durationInFrames`, set by the render request's time).
 * A text effect's text block (`input.text`) is laid out by the host instead: it splits `text` into one tier or an
 * upper and a lower tier, cuts each tier at the keyword hits, stacks the tiers in one column of the block and hands
 * each tier to its text Preset, keyword hits to the keyword Preset, and wraps the block, the tiers and the keyword
 * hits in the effect Presets bound there (see `textEntry`). It writes `{code, children}`: the Sprite source and, for each
 * tier, whether its text Preset takes the host-rendered line as `children` (see `takesChildren`).
 */
import { build } from "esbuild";
import fs from "node:fs";
import ts from "typescript";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const block = input.text ?? null;
const instances = block ? [...block.tiers, ...(block.keyword ? [block.keyword] : []), ...(block.effects ?? [])] : Array.isArray(input.instances) ? input.instances : [];
// Instances of the same Preset share one bundled module, so repeated instances do not repeat its source.
const modules = [...new Set(instances.map((item) => item.code))];
/**
 * Whether a text Preset draws the line the host renders and passes as `children` rather than drawing the `segments`
 * itself: such a Preset names `children` somewhere in its code (its props type, `props.children` or a destructured
 * binding), which a Preset written for `segments` has no reason to do. Comments and strings do not count.
 */
function takesChildren(code) {
  const source = ts.createSourceFile("Preset.tsx", code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found = false;
  const visit = (node) => { if (found) return; if (ts.isIdentifier(node) && node.text === "children") found = true; else ts.forEachChild(node, visit); };
  visit(source);
  return found;
}
const children = block ? block.tiers.map((item) => takesChildren(item.code)) : [];
/** The object of this instance's Preset props that top-level parameters bind, read from `props`. */
function boundProps(item) {
  return `{${(item.bindings ?? []).map(({ name, parameter }) => `${JSON.stringify(parameter)}: props[${JSON.stringify(name)}]`).join(", ")}}`;
}
/** Pass each top-level parameter bound to this instance to its Preset prop, after the nested props so it wins. */
function bound(item) {
  return item.bindings?.length ? ` {...${boundProps(item)}}` : "";
}
// The previewed length: the latest instance end, as the Agent composed it.
// A text block spans the whole Sprite, so only box instances have a previewed length to lay out against.
const authored = block ? 1 : Math.max(1, ...instances.map((item) => item.timing.start_frame + item.timing.duration_frames));
// Shared by both entries: the defaults the version accepted and the nested merge of caller props over them.
const prelude = [
  ...modules.map((_, index) => `import Preset${index} from "imv:preset/${index}";`),
  `const __defaults = ${JSON.stringify(input.defaults ?? {})};`,
  `const __merge = (base, patch) => { if (patch === undefined || patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch === undefined ? base : patch; const result = {...base}; for (const [key, value] of Object.entries(patch)) result[key] = value && typeof value === "object" && !Array.isArray(value) && result[key] && typeof result[key] === "object" && !Array.isArray(result[key]) ? __merge(result[key], value) : value; return result; };`,
];

/**
 * The host of a text block. Configuration comes from top-level props with fixed names: `text` (tiers split on \n),
 * `fontSize`, one tier's `fontFamily`/`fontWeight` or each tier's `line{i}FontFamily`/`line{i}FontWeight`/
 * `line{i}SizeRatio`, `lineGap`, `lineStartFrames`, the keyword list with its six IMS styles and `keywordEffect`.
 * The first literal hit of each keyword in the whole text (earliest first, never overlapping) becomes a keyword
 * segment; the host wraps it in the IMS styles and draws it with the keyword Preset while `keywordEffect` is on.
 * Tier i stays at its first frame, invisible but keeping its space, until `lineStartFrames[i]`, then plays from there.
 * Two tiers are stacked `lineGap` apart, or, with `tierLayout` "overlap", share one grid cell, centred on each other
 * with the lower tier drawn over the upper one. The element around each tier carries the tier's font size, so an effect
 * there measures in its em.
 *
 * A tier whose text Preset takes `children` gets its line from the host: the text with each keyword hit drawn by the
 * host's `__Keyword`; while an effect at `chars` is on, every glyph (a grapheme; a keyword hit counts as one, spaces
 * none) is a unit of its own. Other text Presets draw the `segments` themselves and offer no `chars` position.
 *
 * Effect k (from 1, in the block's order, the first innermost) wraps every position its `effect{k}Target` names while
 * its switch `effect{k}` is on: `block` the whole column, `tiers` each tier, `line1`/`line2` one tier, `chars` each
 * unit of a tier's line and `keyword` each keyword hit. A tier and its keyword hits play inside the tier's Sequence, so their frames count from the tier's
 * start and the Sequence ends with the Sprite. The effect Preset receives the wrapped children, the host's Box (a
 * full-width block, or an inline block for a keyword hit) and `progress`, how far the position is shown: an entrance
 * rises from 0 at the position's first frame to 1 after `effect{k}Seconds`, an exit falls to 0 on the last frame, a
 * looping effect stays at 1, and a cue effect stays at 0 until the lower tier appears and then rises to 1 over its
 * length (it never rises while the lower tier has no text). Each length is at most half of the frames the position plays less one, so short renders
 * and a text frozen on its middle frame (`D // 2` or `(D - 1) // 2` of D frames) still show it in full there. At `chars`
 * the units share that length in turn: unit i of n shows `clamp(P * n - i)` of the position's progress P, so an
 * entrance reveals them in reading order, an exit removes the last one first, and a longer text goes faster. Every
 * effect Preset also receives `index` and `count`: the unit's place in its tier at `chars`, the tier's place at `tiers`
 * and `line1`/`line2`, and 0 of 1 elsewhere.
 *
 * A still block (`block.still`, a work made with effects) holds each tier Preset on the middle frame of the frames the
 * tier plays, so any motion of its own never plays and the effects alone move it; the keyword hits and the units inside
 * get the tier's live frame back (`__Live`), so a keyword Preset and the effects at `keyword` and `chars` keep playing.
 * An older block plays its tier Presets' own motion.
 */
function textEntry() {
  const tiers = block.tiers.length;
  const keyword = block.keyword ? modules.indexOf(block.keyword.code) : -1;
  const effect = block.keyword ? `{...props[${JSON.stringify(block.keyword.instance_id)}], ...${boundProps(block.keyword)}}` : "{}";
  const effects = (block.effects ?? []).map((item, index) => `{k: ${index + 1}, slot: ${JSON.stringify(item.slot)}, Preset: Preset${modules.indexOf(item.code)}, `
    + `props: (props) => ({...props[${JSON.stringify(item.instance_id)}], ...${boundProps(item)}})}`);
  // Positions the tier's own wrapper takes: all tiers, and the upper or lower one of two.
  const at = (index) => JSON.stringify(tiers === 1 ? [] : ["tiers", `line${index + 1}`]);
  const hold = (element) => (block.still ? `<__Hold>${element}</__Hold>` : element);
  const tier = (item, index) => {
    const size = tiers === 1 ? "props.fontSize" : `props.fontSize * props.line${index + 1}SizeRatio`;
    const font = tiers === 1
      ? `fontFamily: props.fontFamily, fontWeight: props.fontWeight, fontSize: ${size}`
      : `fontFamily: props.line${index + 1}FontFamily, fontWeight: props.line${index + 1}FontWeight, fontSize: ${size}`;
    const start = tiers === 1 ? "0" : `(Array.isArray(props.lineStartFrames) && Number.isFinite(props.lineStartFrames[${index}]) ? props.lineStartFrames[${index}] : 0)`;
    // A Preset that takes `children` is handed the host's line; the others draw the segments themselves.
    const line = children[index] ? `{__line(__segments(__lineText, __offsets[${index}], __found), __glyphs)}` : "";
    return `(() => { const __lineText = __lines[${index}]; if (!__lineText) return null; const __start = ${start}; const __waiting = __frame < __start; `
      // A negative `lineGap` pulls a lower tier up over the upper one; CSS `gap` cannot be negative, so the spacing is a margin.
      + `return <div key={${JSON.stringify(item.instance_id)}} style={{width: "100%", gridArea: __overlap ? "1 / 1" : undefined, marginTop: ${index === 0 ? 0 : "__overlap ? 0 : props.lineGap"}, fontSize: ${size}, opacity: __waiting ? 0 : 1}}>`
      + `<Freeze frame={__start} active={__waiting}><Sequence from={__start} durationInFrames={Math.max(1, __total - __start)} layout="none"><__Origin.Provider value={__start}>`
      + `<__Effects at={${at(index)}} index={${index}} count={${tiers}}>${hold(`<Preset${modules.indexOf(item.code)} {...props[${JSON.stringify(item.instance_id)}]}${bound(item)} {...{text: __lineText, segments: __segments(__lineText, __offsets[${index}], __found), Keyword: __Keyword, ${font}}}>${line}</Preset${modules.indexOf(item.code)}>`)}</__Effects>`
      + `</__Origin.Provider></Sequence></Freeze></div>; })()`;
  };
  const { x, y, width, height } = block.layout;
  return [
    'import React from "react";',
    'import {AbsoluteFill, Freeze, Sequence, useCurrentFrame, useVideoConfig} from "remotion";',
    ...prelude,
    `const __hits = (text, keywords) => { const hits = []; for (const word of Array.isArray(keywords) ? keywords : []) { if (typeof word !== "string" || !word) continue; for (let at = text.indexOf(word); at >= 0; at = text.indexOf(word, at + 1)) { if (!hits.some(([start, end]) => at < end && at + word.length > start)) { hits.push([at, at + word.length]); break; } } } return hits.sort((left, right) => left[0] - right[0]); };`,
    `const __segments = (line, offset, hits) => { const out = []; let at = 0; for (const [start, end] of hits) { const from = Math.max(start - offset, at), to = Math.min(end - offset, line.length); if (to <= from) continue; if (from > at) out.push({text: line.slice(at, from), keyword: false}); out.push({text: line.slice(from, to), keyword: true}); at = to; } if (at < line.length) out.push({text: line.slice(at), keyword: false}); return out; };`,
    `const __Context = React.createContext({props: {}, effect: {}, cue: null});`,
    // The frame the position's own frames count from: its tier's start, 0 for the block.
    `const __Origin = React.createContext(0);`,
    `const __effects = [${effects.join(", ")}];`,
    // The host's elements an effect styles: a full-width block around the block or a tier, an inline block around a keyword hit.
    `function __BlockBox({style, children}) { return <div style={{...style, width: "100%"}}>{children}</div>; }`,
    `function __InlineBox({style, children}) { return <span style={{...style, display: "inline-block"}}>{children}</span>; }`,
    `const __clamp = (value) => Math.min(1, Math.max(0, value));`,
    // A held tier's live frame, for the keyword hits and units drawn inside it; null outside a held tier.
    `const __Live = React.createContext(null);`,
    `function __Hold({children}) { const frame = useCurrentFrame(); const {durationInFrames} = useVideoConfig(); return <__Live.Provider value={frame}><Freeze frame={Math.max(0, Math.floor((durationInFrames - 1) / 2))}>{children}</Freeze></__Live.Provider>; }`,
    `function __Effects({at, inline, index = 0, count = 1, children}) { const {props, cue} = React.useContext(__Context); const origin = React.useContext(__Origin); const frame = useCurrentFrame(); const {fps, durationInFrames} = useVideoConfig(); let node = children; `
      + `for (const item of __effects) { if (props["effect" + item.k] === false || !at.includes(props["effect" + item.k + "Target"])) continue; `
      + `const length = Math.max(1, Math.min(Math.round((Number(props["effect" + item.k + "Seconds"]) || 0.5) * fps), Math.floor((durationInFrames - 1) / 2))); `
      + `const shown = item.slot === "enter" ? __clamp(frame / length) : item.slot === "exit" ? __clamp((durationInFrames - 1 - frame) / length) : item.slot === "cue" ? (cue === null ? 0 : __clamp((frame - (cue - origin)) / length)) : 1; `
      // The units of a line take the position's progress in turn.
      + `const progress = item.slot !== "loop" && at.includes("chars") ? __clamp(shown * count - index) : shown; `
      + `node = <item.Preset {...item.props(props)} progress={progress} index={index} count={count} Box={inline ? __InlineBox : __BlockBox}>{node}</item.Preset>; } return node; }`,
    // Whether an effect at `chars` is on: only then is a line cut into units, so its text otherwise shapes as one run.
    `const __glyphsOn = (props) => __effects.some((item) => props["effect" + item.k] !== false && props["effect" + item.k + "Target"] === "chars");`,
    `const __split = (text) => typeof Intl.Segmenter === "function" ? Array.from(new Intl.Segmenter(undefined, {granularity: "grapheme"}).segment(text), (part) => part.segment) : Array.from(text);`,
    // One unit of a line at the position `chars`, at its held tier's live frame like a keyword hit.
    `function __Unit({index, count, children}) { const live = React.useContext(__Live); const inner = <__Effects at={["chars"]} inline index={index} count={count}>{children}</__Effects>; return live === null ? inner : <Freeze frame={live}>{inner}</Freeze>; }`,
    // The line handed to a text Preset as children: its plain text, each keyword hit drawn by the host and, while
    // an effect at `chars` is on, every glyph and every hit a unit (spaces stay plain text between units).
    `function __line(segments, glyphs) { if (!glyphs) return segments.map((segment, at) => segment.keyword ? <__Keyword key={at} text={segment.text} /> : segment.text); `
      + `const parts = segments.flatMap((segment) => segment.keyword ? [segment] : __split(segment.text).map((text) => ({text, keyword: false}))); const count = parts.filter((part) => part.keyword || part.text.trim()).length; let index = 0; `
      + `return parts.map((part, at) => part.keyword ? <__Unit key={at} index={index++} count={count}><__Keyword text={part.text} /></__Unit> : part.text.trim() ? <__Unit key={at} index={index++} count={count}><span data-imv-glyph="">{part.text}</span></__Unit> : part.text); }`,
    // The six keyword options of the bus's IMS text: bold, italic, underline, strikeout, colour ("" keeps the text's) and size (0 keeps it).
    `function __Keyword({text}) { const {props, effect} = React.useContext(__Context); const style = {fontWeight: props.keywordBold ? 700 : undefined, fontStyle: props.keywordItalic ? "italic" : undefined, textDecorationLine: [props.keywordUnderline && "underline", props.keywordStrikeout && "line-through"].filter(Boolean).join(" ") || undefined, color: props.keywordColor || undefined, fontSize: props.keywordSize > 0 ? props.keywordSize : undefined}; const live = React.useContext(__Live); const inner = <__Effects at={["keyword"]} inline>{${keyword >= 0 ? `props.keywordEffect !== false ? <Preset${keyword} {...effect} text={text} /> : text` : "text"}}</__Effects>; return <span data-imv-keyword="" style={style}>{live === null ? inner : <Freeze frame={live}>{inner}</Freeze>}</span>; }`,
    `export default function Sprite(inputProps = {}) { const props = __merge(__defaults, inputProps); const __frame = useCurrentFrame(); const {durationInFrames: __total} = useVideoConfig(); `
      + `const __glyphs = __glyphsOn(props); const __text = typeof props.text === "string" ? props.text : ""; const __lines = ${tiers === 1 ? "[__text]" : "__text.split(\"\\n\").slice(0, 2)"}; const __offsets = __lines.map((_, index) => __lines.slice(0, index).reduce((sum, line) => sum + line.length + 1, 0)); const __found = __hits(__text, props.keywords); `
      // The block frame the lower tier appears at, for cue effects; none while the lower tier has no text.
      + `const __overlap = __lines.length > 1 && props.tierLayout === "overlap"; const __cue = __lines[1] ? (Array.isArray(props.lineStartFrames) && Number.isFinite(props.lineStartFrames[1]) ? props.lineStartFrames[1] : 0) : null; `
      + `return <__Context.Provider value={{props, effect: ${effect}, cue: __cue}}><AbsoluteFill><div data-imv-instance="text" style={{position: "absolute", left: ${x}, top: ${y}, width: ${width}, height: ${height}, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center"}}>`
      + `<__Effects at={["block"]}><div style={__overlap ? {width: "100%", display: "grid", alignItems: "center", justifyItems: "center"} : {width: "100%", display: "flex", flexDirection: "column", alignItems: "center"}}>`
      + `${block.tiers.map((item, index) => `{${tier(item, index)}}`).join("")}</div></__Effects></div></AbsoluteFill></__Context.Provider>; }`,
  ].join("\n");
}

const entry = block ? textEntry() : [
  'import React from "react";',
  'import {AbsoluteFill, Sequence, useVideoConfig} from "remotion";',
  `const __authored = ${authored};`,
  // Longer renders keep each instance's start and length, and instances that ran to the previewed end run to the
  // new end; shorter renders scale every instance by the same ratio, keeping at least one frame each.
  `const __span = (start, length, total) => { if (total >= __authored) return [start, start + length === __authored ? total - start : length]; const from = Math.floor(start * total / __authored); return [from, Math.max(1, Math.floor((start + length) * total / __authored) - from)]; };`,
  ...prelude,
  `export default function Sprite(inputProps = {}) { const props = __merge(__defaults, inputProps); const {durationInFrames: __total} = useVideoConfig(); const __t = [${instances.map((item) => `__span(${item.timing.start_frame}, ${item.timing.duration_frames}, __total)`).join(", ")}]; return <AbsoluteFill>${instances.map((item, index) => `<Sequence key={${JSON.stringify(item.instance_id)}} from={__t[${index}][0]} durationInFrames={__t[${index}][1]} width={${item.layout.width}} height={${item.layout.height}} style={{position:"absolute",left:${item.layout.x},top:${item.layout.y},width:${item.layout.width},height:${item.layout.height},zIndex:${item.layout.z_index}}}><div data-imv-instance={${JSON.stringify(item.instance_id)}} style={{width:"100%",height:"100%"}}><Preset${modules.indexOf(item.code)} {...props[${JSON.stringify(item.instance_id)}]}${bound(item)} /></div></Sequence>`).join("")}</AbsoluteFill>; }`,
].join("\n");

const result = await build({
  stdin: { contents: entry, sourcefile: "Sprite.tsx", loader: "tsx" },
  bundle: true,
  write: false,
  format: "esm",
  platform: "browser",
  jsx: "automatic",
  external: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime", "remotion"],
  plugins: [{
    name: "imv-preset-modules",
    setup(plugin) {
      plugin.onResolve({ filter: /^imv:preset\// }, (args) => ({ path: args.path, namespace: "imv-preset" }));
      plugin.onResolve({filter: /.*/}, args => { if (["react", "react/jsx-runtime", "react/jsx-dev-runtime", "remotion"].includes(args.path)) return {path:args.path,external:true}; throw new Error("Unsupported module: " + args.path); });
      plugin.onLoad({ filter: /.*/, namespace: "imv-preset" }, (args) => {
        const index = Number(args.path.split("/").pop());
        const source = modules[index];
        if (typeof source !== "string") throw new Error(`Missing Preset module ${index}`);
        return { contents: source, loader: "tsx", resolveDir: process.cwd() };
      });
    },
  }],
});
const source = ts.createSourceFile("Sprite.tsx", result.outputFiles[0]?.text ?? "", ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
// esbuild erases types and rewrites the default export. Restore generated function
// boundaries, variables and class members with the parser, so defaults, destructuring, recursive helpers and
// declarations typed only by an erased annotation or type argument (`const hits: T[] = []`, `useState<T | null>(null)`,
// `constructor(private size: number)`) remain valid.
// Original Preset modules are independently typechecked before the generated adapter.
const transformed = ts.transform(source, [(context) => {
  /** True for a for-in/for-of binding, where a type annotation is a syntax error. */
  function loopBinding(parent) {
    return Boolean(parent && ts.isVariableDeclarationList(parent) && ts.isForInOrOfStatement(parent.parent));
  }
  /** Normalize emitted JavaScript without running any Preset code. */
  function visit(node) {
    const parent = node.parent;
    node = ts.visitEachChild(node, visit, context);
    const any = () => ts.factory.createKeywordTypeNode(ts.SyntaxKind.AnyKeyword);
    if (ts.isVariableDeclaration(node) && !node.type && !loopBinding(parent)) {
      return ts.factory.updateVariableDeclaration(node, node.name, node.exclamationToken, any(), node.initializer);
    }
    if (ts.isPropertyDeclaration(node) && !node.type) {
      return ts.factory.updatePropertyDeclaration(node, node.modifiers, node.name, node.questionToken ?? node.exclamationToken, any(), node.initializer);
    }
    // Parameter properties and `declare` fields leave no member behind, only `this.size = size`.
    // `extends React.Component<P, S>` loses its type arguments, so the base class is typed `any` too.
    if (ts.isHeritageClause(node) && node.token === ts.SyntaxKind.ExtendsKeyword) {
      return ts.factory.updateHeritageClause(node, node.types.map((base) => ts.factory.updateExpressionWithTypeArguments(base,
        ts.factory.createParenthesizedExpression(ts.factory.createAsExpression(base.expression, any())), base.typeArguments)));
    }
    if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      const key = ts.factory.createParameterDeclaration(undefined, undefined, "key", undefined, ts.factory.createKeywordTypeNode(ts.SyntaxKind.StringKeyword));
      const members = [ts.factory.createIndexSignature(undefined, [key], any()), ...node.members];
      return ts.isClassDeclaration(node)
        ? ts.factory.updateClassDeclaration(node, node.modifiers, node.name, node.typeParameters, node.heritageClauses, members)
        : ts.factory.updateClassExpression(node, node.modifiers, node.name, node.typeParameters, node.heritageClauses, members);
    }
    if (ts.isParameter(node)) {
      return ts.factory.updateParameterDeclaration(node, node.modifiers, node.dotDotDotToken, node.name, node.questionToken,
        node.dotDotDotToken ? ts.factory.createArrayTypeNode(any()) : any(), node.initializer);
    }
    if (ts.isFunctionDeclaration(node)) {
      return ts.factory.updateFunctionDeclaration(node, node.modifiers, node.asteriskToken, node.name, node.typeParameters, node.parameters, any(), node.body);
    }
    if (ts.isFunctionExpression(node)) {
      return ts.factory.updateFunctionExpression(node, node.modifiers, node.asteriskToken, node.name, node.typeParameters, node.parameters, any(), node.body);
    }
    if (ts.isArrowFunction(node)) {
      return ts.factory.updateArrowFunction(node, node.modifiers, node.typeParameters, node.parameters, any(), node.equalsGreaterThanToken, node.body);
    }
    if (ts.isExportDeclaration(node) && !node.moduleSpecifier && node.exportClause && ts.isNamedExports(node.exportClause)) {
      const defaultExport = node.exportClause.elements.find((item) => item.name.text === "default");
      if (defaultExport) {
        const remaining = node.exportClause.elements.filter((item) => item !== defaultExport);
        const assignment = ts.factory.createExportAssignment(undefined, false, defaultExport.propertyName ?? defaultExport.name);
        return remaining.length ? [ts.factory.updateExportDeclaration(node, node.modifiers, node.isTypeOnly, ts.factory.updateNamedExports(node.exportClause, remaining), undefined, node.attributes), assignment] : assignment;
      }
    }
    return node;
  }
  return (root) => ts.visitNode(root, visit);
}]);
const code = ts.createPrinter().printFile(transformed.transformed[0]);
transformed.dispose();
process.stdout.write(JSON.stringify({ code, children }));
