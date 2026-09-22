// usage: start <YYYY-MM-DD> [--force] [--resume N]   starts one DigestWorkflow and waits for its result
import { connect, startDigest } from "../client.js";
const date = process.argv[2];
if (!date) throw new Error("usage: start <YYYY-MM-DD> [--force] [--resume N]");
const force = process.argv.includes("--force");
const r = process.argv.indexOf("--resume");
const resumeRun = r > 0 ? Number(process.argv[r + 1]) : undefined;
const handle = await startDigest(await connect(), date, { force, ...(resumeRun !== undefined ? { resumeRun } : {}) });
console.log(`started ${handle.workflowId}`);
console.log(JSON.stringify(await handle.result()));
