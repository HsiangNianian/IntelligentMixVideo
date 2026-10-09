/** Bundle Preset TSX modules into one Sprite module without executing generated code. */
import { build } from "esbuild";
import fs from "node:fs";
import ts from "typescript";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const instances = Array.isArray(input.instances) ? input.instances : [];
const entry = [
  'import React from "react";',
  'import {AbsoluteFill, Sequence} from "remotion";',
  ...instances.map((_, index) => `import Preset${index} from "imv:preset/${index}";`),
  `const __defaults = ${JSON.stringify(input.defaults ?? {})};`,
  `const __merge = (base, patch) => { if (patch === undefined || patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch === undefined ? base : patch; const result = {...base}; for (const [key, value] of Object.entries(patch)) result[key] = value && typeof value === "object" && !Array.isArray(value) && result[key] && typeof result[key] === "object" && !Array.isArray(result[key]) ? __merge(result[key], value) : value; return result; };`,
  `export default function Sprite(inputProps = {}) { const props = __merge(__defaults, inputProps); return <AbsoluteFill>${instances.map((item, index) => `<Sequence key={${JSON.stringify(item.instance_id)}} from={${item.timing.start_frame}} durationInFrames={${item.timing.duration_frames}} width={${item.layout.width}} height={${item.layout.height}} style={{position:"absolute",left:${item.layout.x},top:${item.layout.y},width:${item.layout.width},height:${item.layout.height},zIndex:${item.layout.z_index},overflow:"hidden"}}><div data-imv-instance={${JSON.stringify(item.instance_id)}} style={{width:"100%",height:"100%"}}><Preset${index} {...props[${JSON.stringify(item.instance_id)}]} /></div></Sequence>`).join("")}</AbsoluteFill>; }`,
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
        const source = instances[index]?.code;
        if (typeof source !== "string") throw new Error(`Missing Preset module ${index}`);
        return { contents: source, loader: "tsx", resolveDir: process.cwd() };
      });
    },
  }],
});
const source = ts.createSourceFile("Sprite.tsx", result.outputFiles[0]?.text ?? "", ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
// esbuild erases types and rewrites the default export. Restore generated function
// boundaries with the parser so defaults, destructuring and recursive helpers remain valid.
// Original Preset modules are independently typechecked before the generated adapter.
const transformed = ts.transform(source, [(context) => {
  /** Normalize emitted JavaScript without running any Preset code. */
  function visit(node) {
    node = ts.visitEachChild(node, visit, context);
    const any = () => ts.factory.createKeywordTypeNode(ts.SyntaxKind.AnyKeyword);
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
