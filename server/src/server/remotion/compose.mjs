/**
 * Bundle Preset TSX modules into one Sprite module without executing generated code.
 * The Agent times instances against the length the user previewed; the generated host lays them out against the
 * length it is actually rendered at (`useVideoConfig().durationInFrames`, set by the render request's time).
 */
import { build } from "esbuild";
import fs from "node:fs";
import ts from "typescript";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const instances = Array.isArray(input.instances) ? input.instances : [];
// Instances of the same Preset share one bundled module, so repeated instances do not repeat its source.
const modules = [...new Set(instances.map((item) => item.code))];
/** Pass each top-level parameter bound to this instance to its Preset prop, after the nested props so it wins. */
function bound(item) {
  const pairs = (item.bindings ?? []).map(({ name, parameter }) => `${JSON.stringify(parameter)}: props[${JSON.stringify(name)}]`);
  return pairs.length ? ` {...{${pairs.join(", ")}}}` : "";
}
// The previewed length: the latest instance end, as the Agent composed it.
const authored = Math.max(1, ...instances.map((item) => item.timing.start_frame + item.timing.duration_frames));
const entry = [
  'import React from "react";',
  'import {AbsoluteFill, Sequence, useVideoConfig} from "remotion";',
  `const __authored = ${authored};`,
  // Longer renders keep each instance's start and length, and instances that ran to the previewed end run to the
  // new end; shorter renders scale every instance by the same ratio, keeping at least one frame each.
  `const __span = (start, length, total) => { if (total >= __authored) return [start, start + length === __authored ? total - start : length]; const from = Math.floor(start * total / __authored); return [from, Math.max(1, Math.floor((start + length) * total / __authored) - from)]; };`,
  ...modules.map((_, index) => `import Preset${index} from "imv:preset/${index}";`),
  `const __defaults = ${JSON.stringify(input.defaults ?? {})};`,
  `const __merge = (base, patch) => { if (patch === undefined || patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch === undefined ? base : patch; const result = {...base}; for (const [key, value] of Object.entries(patch)) result[key] = value && typeof value === "object" && !Array.isArray(value) && result[key] && typeof result[key] === "object" && !Array.isArray(result[key]) ? __merge(result[key], value) : value; return result; };`,
  `export default function Sprite(inputProps = {}) { const props = __merge(__defaults, inputProps); const {durationInFrames: __total} = useVideoConfig(); const __t = [${instances.map((item) => `__span(${item.timing.start_frame}, ${item.timing.duration_frames}, __total)`).join(", ")}]; return <AbsoluteFill>${instances.map((item, index) => `<Sequence key={${JSON.stringify(item.instance_id)}} from={__t[${index}][0]} durationInFrames={__t[${index}][1]} width={${item.layout.width}} height={${item.layout.height}} style={{position:"absolute",left:${item.layout.x},top:${item.layout.y},width:${item.layout.width},height:${item.layout.height},zIndex:${item.layout.z_index},overflow:"hidden"}}><div data-imv-instance={${JSON.stringify(item.instance_id)}} style={{width:"100%",height:"100%"}}><Preset${modules.indexOf(item.code)} {...props[${JSON.stringify(item.instance_id)}]}${bound(item)} /></div></Sequence>`).join("")}</AbsoluteFill>; }`,
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
// boundaries and variables with the parser, so defaults, destructuring, recursive helpers and variables
// typed only by an erased annotation or type argument (`const hits: T[] = []`, `useState<T | null>(null)`) remain valid.
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
process.stdout.write(JSON.stringify({ code }));
