import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Test support: the workflow as a later build that changes WORKFLOW code would ship it, with one
// activity call added before the pre-broadcast hold. Written under dist/ (the build output, never
// committed; the bundler compiles no TypeScript under node_modules), with the relative imports made absolute.
const here = dirname(fileURLToPath(import.meta.url));
const ANCHOR = "    const hold = holdFor();\n";
export function changedWorkflowPath(): string {
  const src = readFileSync(join(here, "digest.workflow.ts"), "utf8");
  if (!src.includes(ANCHOR)) throw new Error("the hold's anchor line is gone from digest.workflow.ts; update deploy-variant.ts");
  const changed = src
    .replace(ANCHOR, `    await ops.healthcheckLog("added by a later deploy");\n${ANCHOR}`)
    .replaceAll('from "./', `from "${here}/`)
    .replaceAll('from "../', `from "${dirname(here)}/`);
  const dir = join(here, "..", "..", "dist", "deploy-variant");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "digest.workflow.ts");
  writeFileSync(path, changed);
  return path;
}
