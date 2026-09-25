// usage: deploy-guard [--force]   exit 0 when a deploy may go ahead: outside 12:00-13:45 Europe/Paris
// and no DigestWorkflow running on the Temporal at TEMPORAL_ADDRESS. Otherwise names every reason and
// exits 1; --force names them and exits 0. seanfloyd-infra's Kamal pre-deploy hook runs it.
import { connect } from "../client.js";
import { guard } from "../deploy-guard.js";

const force = process.argv.includes("--force");
const problems = await guard(await connect());
for (const p of problems) console.log(`${force ? "deploying anyway (--force)" : "refused"}: ${p}`);
if (problems.length === 0) console.log("deploy-guard: no run in flight, outside the run window");
process.exitCode = problems.length && !force ? 1 : 0;
