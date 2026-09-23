import { openDb, dbPath } from "../store/db.js";
import { recordUsage, type UsageRow } from "../store/usage.js";
import { Context, heartbeat } from "@temporalio/activity";
import { ArtifactStore } from "../store/artifacts.js";
import type { Activities } from "./index.js";
import { assembleActivity } from "./assemble.js";
import { clusterActivities } from "./cluster.js";
import { coherenceActivity } from "./coherence.js";
import { preheaderActivity } from "./preheader.js";
import { prepareActivity } from "./prepare.js";
import { recapActivity } from "./recap.js";
import { repairActivity } from "./repair.js";
import { MODEL_MAX_ATTEMPTS } from "../workflow/policy.js";
import { selectActivity } from "./select.js";
import { writeActivities } from "./write.js";
import { stubActivities } from "./stub.js";

export const DEFAULT_AGENTS_DIR = "/app/digest/agents";
const agentsDir = (): string => process.env["AGENTS_DIR"] ?? DEFAULT_AGENTS_DIR;

// The worker's activity set: real activities as they are ported (plan A2), stubs for the rest.
const safeHeartbeat = () => {
  try {
    heartbeat();
  } catch {
    /* outside an activity context (tests, CLIs) there is nothing to beat */
  }
};
const safeSignal = (): AbortSignal | undefined => {
  try {
    return Context.current().cancellationSignal;
  } catch {
    return undefined; // outside an activity there is nothing to cancel
  }
};

export function workerActivities(): Activities {
  const store = new ArtifactStore(dbPath());
  const usageDb = openDb(dbPath());
  const log = (row: UsageRow) => recordUsage(usageDb, row);
  const deps = { store, agentsDir: agentsDir(), heartbeat: safeHeartbeat, signal: safeSignal, onUsage: log };
  return { ...stubActivities(), ...clusterActivities(deps), recap: recapActivity(deps), ...writeActivities(deps), select: selectActivity(deps), preheader: preheaderActivity(deps), coherence: coherenceActivity(deps), repair: repairActivity({ ...deps, maxAttempts: MODEL_MAX_ATTEMPTS }), assemble: assembleActivity(deps), prepare: prepareActivity({ store, dbPath: dbPath() }) };
}
