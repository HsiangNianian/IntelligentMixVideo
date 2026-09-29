/** Bundle one immutable published Sprite into an isolated browser preview; never expose TSX to the client. */
import fs from "node:fs/promises";
import os from "node:os";
import { build } from "esbuild";

const root = process.env.IMV_WORK_ROOT ?? "/work";
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? "/renderer";
// Keep the interactive preview browser profile inside this isolated attempt.
os.tmpdir = () => `${root}/.tmp`;
const request = JSON.parse(await fs.readFile(`${root}/request.json`, "utf8"));
const result = { checks: [] };

/** Resolve only the trusted host's two virtual imports and the validated published source. */
async function bundlePreview() {
  await fs.writeFile(`${root}/Template.tsx`, request.code);
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
  const output = await build({
    entryPoints: [`${rendererRoot}/sprite-preview-host.tsx`],
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    target: "es2022",
    minify: true,
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    plugins: [{
      name: "published-sprite",
      setup(builder) {
        builder.onResolve({ filter: /^imv:template$/ }, () => ({ path: `${root}/Template.tsx` }));
        builder.onResolve({ filter: /^imv:config$/ }, () => ({ path: "config", namespace: "imv" }));
        builder.onLoad({ filter: /.*/, namespace: "imv" }, () => ({
          contents: JSON.stringify(request.preview), loader: "json",
        }));
      },
    }],
  });
  if (output.outputFiles.length !== 1 || !output.outputFiles[0].text)
    throw new Error("Sprite preview bundle is empty");
  await fs.writeFile(`${root}/interactive.js`, output.outputFiles[0].text);
}

try {
  await bundlePreview();
  result.checks.push({ name: "interactive_bundle", status: "pass" });
} catch (error) {
  result.checks.push({ name: "interactive_bundle", status: "fail", detail: String(error).slice(0, 2000) });
  process.exitCode = 1;
} finally {
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify(result));
}
