/** Exercise real language-service diagnostics with trusted fixture source; no generated TSX is executed. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { languageDiagnostics } from "./typescript.mjs";

/** Create an isolated declaration-resolution fixture and always remove its temporary files. */
async function withSource(source, contract, verify) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "imv-typescript-"));
  try {
    await fs.symlink(
      fileURLToPath(new URL("./node_modules", import.meta.url)),
      path.join(root, "node_modules"),
    );
    await fs.writeFile(path.join(root, "Template.tsx"), source);
    await fs.writeFile(path.join(root, "contract.tsx"), contract);
    await fs.writeFile(
      path.join(root, "Export.tsx"),
      "export { default } from './Template';",
    );
    verify(languageDiagnostics(root));
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
}

test("typed scalar props compile using real React declarations", async () => {
  await withSource(
    "export default function Template(p: {text: string}) {return <div>{p.text}</div>;}",
    "import Template from './Template'; const element = <Template text='hello' />;",
    (result) => assert.deepEqual(result.diagnostics, []),
  );
});

test("missing host prop produces structured file, code, severity and one-based location", async () => {
  await withSource(
    "export default function Template(p: {text: string}) {return <div>{p.text}</div>;}",
    "import Template from './Template';\nconst element = <Template />;",
    (result) => {
      const diagnostic = result.diagnostics.find((item) => item.code === 2741);
      assert.equal(diagnostic.file, "contract.tsx");
      assert.equal(diagnostic.line, 2);
      assert.equal(diagnostic.severity, "error");
      assert.ok(diagnostic.column > 0);
      assert.match(diagnostic.message, /text/);
      assert.match(result.detail, /error TS2741/);
    },
  );
});

test("source type failures remain code failures with original diagnostic text", async () => {
  await withSource(
    "const count: number = 'wrong';\nexport default function Template() {return <div>{count}</div>;}",
    "import Template from './Template'; const element = <Template />;",
    (result) => {
      const diagnostic = result.diagnostics.find((item) => item.code === 2322);
      assert.equal(diagnostic.file, "Template.tsx");
      assert.equal(diagnostic.line, 1);
      assert.match(diagnostic.message, /string.*number/);
    },
  );
});
