/** Seal a PR76 component into a Player bundle and an export without screenshots or visual review. */
import fs from 'node:fs/promises';
import {exportTemplate, buildPresentation} from './presentation.mjs';
import {languageDiagnostics} from './typescript.mjs';

const root = process.env.IMV_WORK_ROOT ?? '/work';
const rendererRoot = process.env.IMV_RENDERER_ROOT ?? '/renderer';
/** Build exactly the source and defaults supplied by the publication host. */
async function main() {
  const request = JSON.parse(await fs.readFile(`${root}/request.json`, 'utf8'));
  await fs.writeFile(`${root}/Template.tsx`, request.code);
  await fs.symlink(`${rendererRoot}/node_modules`, `${root}/node_modules`);
  await fs.writeFile(`${root}/Export.tsx`, await exportTemplate(request.code, request.config, request.composition));
  await fs.writeFile(`${root}/contract.tsx`, `/** Verify the standalone export accepts omitted props. */\nimport React from 'react'; import Export from './Export'; const element = <Export />;`);
  const diagnostics = languageDiagnostics(root);
  if (diagnostics.diagnostics.some(item => item.severity === 'error')) throw new Error(diagnostics.detail);
  await buildPresentation(root, request.config, request.composition);
  await fs.writeFile(`${root}/renderer.json`, JSON.stringify({checks: [{name:'export_source', status:'pass'}, {name:'presentation_bundle', status:'pass'}]}));
}
await main();
