// usage: set-current   makes this image's build (GIT_SHA) the current worker deployment version on
// the Temporal at TEMPORAL_ADDRESS, once its worker polls. seanfloyd-infra's bin/deploy-digest runs it (kamal app exec --reuse) inside the running
// worker container, so the build made current is the one that is running.
// Exit 0: current, and no running digest is pinned to another build. 2: current, and the runs listed
// are pinned to a build whose worker is gone (runbook, "Stranded runs"). 3: current, but the running
// digests could not be listed. Anything else: not made current; any run no worker has taken yet is
// listed as "waiting:", since it sits until some build with a worker is current.
import { connect } from "../client.js";
import { deploymentOptions, setCurrentVersion, strandedRuns, waitingLine, waitingRuns } from "../deployment.js";
import { TASK_QUEUE } from "../worker.js";

const { version } = deploymentOptions();
const client = await connect();
try {
  await setCurrentVersion(client, version.buildId, TASK_QUEUE);
} catch (e) {
  // The reason first.
  console.log(`not current: ${String(e)}`);
  try {
    const waiting = await waitingRuns(client);
    if (waiting.length) console.log(waitingLine(waiting));
  } catch (w) {
    console.log(`could not list the runs no worker has taken: ${String(w)}`);
  }
  process.exit(1);
}
console.log(`current version: ${version.deploymentName}:${version.buildId}`);
try {
  const stranded = await strandedRuns(client, version.buildId);
  for (const s of stranded) console.log(`stranded: ${s.workflowId} pinned to ${s.buildId}`);
  process.exitCode = stranded.length > 0 ? 2 : 0;
} catch (e) {
  console.log(`could not list the running digests: ${String(e)}`);
  process.exitCode = 3;
}
