import { heartbeat } from "@temporalio/activity";
import { ArtifactStore } from "../store/artifacts.js";
import { dbPath } from "../store/db.js";
import type { Activities } from "./index.js";
import { clusterActivities } from "./cluster.js";
import { coherenceActivity } from "./coherence.js";
import { preheaderActivity } from "./preheader.js";
import { recapActivity } from "./recap.js";
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
const log = (row: object) => console.log(JSON.stringify({ usage: row }));

export function workerActivities(): Activities {
  const store = new ArtifactStore(dbPath());
  const deps = { store, agentsDir: agentsDir(), heartbeat: safeHeartbeat, onUsage: log };
  return { ...stubActivities(), ...clusterActivities(deps), recap: recapActivity(deps), ...writeActivities(deps), select: selectActivity(deps), preheader: preheaderActivity(deps), coherence: coherenceActivity(deps) };
}
