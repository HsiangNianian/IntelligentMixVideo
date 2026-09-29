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
let code = result.outputFiles[0]?.text ?? "";
// esbuild intentionally emits JavaScript.  Re-add explicit ``any`` annotations
// at generated function boundaries so the saved TSX remains strict-checkable;
// the original Preset source was checked independently before composition.
code = code.replace(/function ([A-Za-z_$][\w$]*)\(([^)]*)\) \{/g, (match, name, args) => {
  if (!args.trim() || args.includes(":")) return match;
  const typed = args.split(",").map((arg) => `${arg.trim()}: any`).join(", ");
  return `function ${name}(${typed}) {`;
});
code = code.replace(/\(([_$A-Za-z][\w$]*)\) =>/g, "($1: any) =>");
code = code.replace(/var __merge = \(base: any, patch: any\) =>/g, "var __merge = (base: Record<string, any>, patch: Record<string, any>) =>");
code = code.replace(/function Sprite\(inputProps: any = \{\}\)/g, "function Sprite(inputProps: Record<string, any> = {})");
process.stdout.write(JSON.stringify({ code }));
