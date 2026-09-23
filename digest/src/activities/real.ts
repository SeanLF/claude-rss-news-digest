import { openDb, dbPath } from "../store/db.js";
import { recordUsage, type UsageRow } from "../store/usage.js";
import { Context, heartbeat } from "@temporalio/activity";
import { ArtifactStore } from "../store/artifacts.js";
import type { Activities } from "./index.js";
import { assembleActivity } from "./assemble.js";
import { clusterActivities } from "./cluster.js";
import { coherenceActivity } from "./coherence.js";
import { fulltextActivities } from "./fulltext.js";
import { preheaderActivity } from "./preheader.js";
import { prepareActivity } from "./prepare.js";
import { runActivities } from "./run.js";
import { recapActivity } from "./recap.js";
import { renderActivity } from "./render.js";
import { envFrom, loadAssets } from "../render/render.js";
import { repairActivity } from "./repair.js";
import { MODEL_MAX_ATTEMPTS, OPS_MAX_ATTEMPTS, WEEKLY_RECAP_MAX_ATTEMPTS } from "../workflow/policy.js";
import { healthcheck, stageDoneLine } from "../ops/healthcheck.js";
import { opsActivities } from "./ops.js";
import { weeklyRecapActivity } from "./weekly-recap.js";
import { selectActivity } from "./select.js";
import { writeActivities } from "./write.js";
import { stubActivities } from "./stub.js";
import { resendClient } from "../mail/resend.js";
import { broadcastActivities, type Mail } from "./broadcast.js";
import { recordActivities } from "./record.js";

export const DEFAULT_AGENTS_DIR = "/app/digest/agents";
const agentsDir = (): string => process.env["AGENTS_DIR"] ?? DEFAULT_AGENTS_DIR;
// The newsroom's template and stylesheet and the shared design tokens, copied into the image.
const renderAssets = () => loadAssets({ templates: process.env["TEMPLATES_DIR"] ?? "/app/newsroom/templates", design: process.env["DESIGN_DIR"] ?? "/app/design" });

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

// Built on first use: the client refuses to construct without RESEND_API_KEY, which a worker with
// the send disabled need not have.
let resend: Mail | undefined;
const mailClient = (): Mail => (resend ??= resendClient(process.env["RESEND_API_KEY"] ?? "", { signal: safeSignal }));

export function workerActivities(): Activities {
  const store = new ArtifactStore(dbPath());
  const usageDb = openDb(dbPath());
  const hc = healthcheck(process.env);
  // Each finished model call is also a progress line off-box, so a hung run is visible while it hangs.
  const log = (row: UsageRow) => {
    recordUsage(usageDb, row);
    void hc.log(stageDoneLine(row));
  };
  const deps = { store, agentsDir: agentsDir(), heartbeat: safeHeartbeat, signal: safeSignal, onUsage: log, log: (m: string) => void hc.log(m) };
  return { ...stubActivities(), ...clusterActivities(deps), recap: recapActivity(deps), ...writeActivities(deps), select: selectActivity(deps), preheader: preheaderActivity(deps), coherence: coherenceActivity(deps), repair: repairActivity({ ...deps, maxAttempts: MODEL_MAX_ATTEMPTS }), assemble: assembleActivity(deps), ...fulltextActivities({ store, perStory: Number(process.env["FULLTEXT_PER_STORY"] ?? 3), enabled: !["0", "false", "no"].includes((process.env["FULLTEXT_ENABLED"] ?? "true").toLowerCase()) }), render: renderActivity({ store, dbPath: dbPath(), assets: renderAssets(), env: envFrom(process.env) }), prepare: prepareActivity({ store, dbPath: dbPath() }), ...runActivities({ store, dbPath: dbPath(), sourcesFile: process.env["SOURCES_FILE"] ?? "/app/sources.json" }), weeklyRecap: weeklyRecapActivity({ ...deps, dbPath: dbPath(), maxAttempts: WEEKLY_RECAP_MAX_ATTEMPTS }), ...opsActivities({ dbPath: dbPath(), env: process.env, maxAttempts: OPS_MAX_ATTEMPTS }), ...recordActivities({ store, dbPath: dbPath() }), ...broadcastActivities({ store, dbPath: dbPath(), mail: mailClient, env: process.env, signal: safeSignal, heartbeat: safeHeartbeat }) };
}
