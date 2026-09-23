import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Test support: the workflow as a deploy that changes WORKFLOW code would ship it, with one
// activity call added before the pre-broadcast hold: bare, or behind patched() as the convention says.
// Written under dist/ (the build output, never committed; the bundler compiles no TypeScript under
// node_modules), with the relative imports made absolute.
const here = dirname(fileURLToPath(import.meta.url));
const ANCHOR = "    const hold = holdFor();\n";
export function changedWorkflowPath(guard: "bare" | "patched" = "bare"): string {
  const src = readFileSync(join(here, "digest.workflow.ts"), "utf8");
  if (!src.includes(ANCHOR)) throw new Error("the hold's anchor line is gone from digest.workflow.ts; update deploy-variant.ts");
  const call = 'await ops.healthcheckLog("added by a later deploy");';
  const changed = src
    .replace(ANCHOR, `    ${guard === "patched" ? `if (patched("deploy-variant")) ${call}` : call}\n${ANCHOR}`)
    .replace(' workflowInfo } from "@temporalio/workflow";', ' workflowInfo, patched } from "@temporalio/workflow";')
    .replaceAll('from "./', `from "${here}/`)
    .replaceAll('from "../', `from "${dirname(here)}/`);
  const dir = join(here, "..", "..", "dist", `deploy-variant-${guard}`);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "digest.workflow.ts");
  writeFileSync(path, changed);
  return path;
}
