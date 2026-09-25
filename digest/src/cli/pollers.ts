// usage: pollers [queue...]   exit 0 when every named task queue (default: python) has a worker polling
// it on the Temporal at TEMPORAL_ADDRESS; otherwise names each one that has none and exits 1.
// seanfloyd-infra's bin/deploy-digest runs it after the workers deploy.
import { connect } from "../client.js";
import { missingPollers } from "../pollers.js";
import { PYTHON_TASK_QUEUE } from "../workflow/policy.js";

const queues = process.argv.length > 2 ? process.argv.slice(2) : [PYTHON_TASK_QUEUE];
const missing = await missingPollers(await connect(), queues);
for (const m of missing) console.log(`missing: ${m}`);
if (missing.length === 0) console.log(`pollers: ${queues.join(", ")} polled`);
process.exitCode = missing.length ? 1 : 0;
