/**
 * Regenerate an accepted component's standalone export with new defaults for an instant parameter revision.
 * It only parses and formats the sealed source on the host, like compose.mjs, and never executes it.
 */
import fs from "node:fs";
import { exportTemplate } from "./presentation.mjs";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({ code: await exportTemplate(input.code, input.config, input.composition) }));
