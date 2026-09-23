import { Context } from "@temporalio/activity";
import { emailSender, resendClient, type SendEmail } from "../mail/resend.js";
import { sendAlert, type AlertRequest } from "../ops/alerts.js";
import { feedHealthAlert } from "../ops/feed-health.js";
import { healthcheck } from "../ops/healthcheck.js";
import { coherenceKindCounts, getRunHealth, threadsEnabled, violations } from "../ops/run-health.js";
import { openDb } from "../store/db.js";

export interface OpsDeps {
  dbPath: string;
  env: Record<string, string | undefined>;
  // The attempts the workflow's alert policy allows; only the last one gives up on a send.
  maxAttempts: number;
  send?: SendEmail;
  fetch?: typeof fetch;
}

const activityInfo = (): { attempt: number; key?: string } => {
  try {
    const { attempt, activityId, workflowExecution: wf } = Context.current().info;
    return { attempt, key: wf ? `${wf.workflowId}/${wf.runId}/${activityId}` : activityId };
  } catch {
    return { attempt: Number.POSITIVE_INFINITY }; // outside an activity every attempt is the last
  }
};

// The operations activities: everything that tells the operator a run went wrong. Every one of them is
// best-effort; none can fail a run that delivered.
export function opsActivities(deps: OpsDeps) {
  const hc = healthcheck(deps.env, deps.fetch);
  const send: SendEmail = deps.send ?? ((email, opts) => emailSender(resendClient(deps.env["RESEND_API_KEY"] ?? "").emails)(email, opts));
  return {
    healthcheck: (event: "start" | "success" | "fail"): Promise<void> => hc.ping(event === "success" ? undefined : event),
    healthcheckLog: (message: string): Promise<void> => hc.log(message),

    // Feeds that keep failing, as run.py alerts on source_health after the fetch.
    checkFeeds: (runId: number, sourceIds: string[]): Promise<AlertRequest | null> => {
      const db = openDb(deps.dbPath);
      try {
        return Promise.resolve(feedHealthAlert(db, runId, sourceIds, Number(deps.env["HEALTH_ALERT_THRESHOLD"] ?? 3)));
      } catch (e) {
        console.error(`feed-health check FAILED to run for run ${runId} (non-fatal): ${String(e)}`);
        return Promise.resolve(null);
      } finally {
        db.close();
      }
    },

    // run.py's _alert_on_run_health and _log_coherence_kinds on a finished run. Not knowing whether an
    // invariant held is strictly better than turning a delivered digest into a failed run.
    checkRunHealth: (runId: number, broadcasting: boolean): Promise<AlertRequest | null> => {
      let db: ReturnType<typeof openDb> | undefined;
      try {
        db = openDb(deps.dbPath);
        const report = db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name='coherence_report.json'").get(runId) as { content: string } | undefined;
        const kinds = coherenceKindCounts(report?.content);
        if (kinds) console.log(JSON.stringify({ stage: "coherence", runId, failureKinds: kinds }));
        const health = getRunHealth(db, runId, { broadcasting, threadsEnabled: threadsEnabled(deps.env), usageRowsDropped: 0 });
        if (health.dropped_continuations) console.warn(`Run ${runId}: ${health.dropped_continuations} story/stories lost a proposed thread continuation to one already claimed this run, and shipped as new threads`);
        const found = violations(health);
        if (!found.length) return Promise.resolve(null);
        // Logged before any send: if the send fails or alerting is off, this line is the only copy.
        console.error(`Run ${runId} violated post-run invariants: ${found.join("; ")}`);
        return Promise.resolve({ kind: "run-health", runId, violations: found });
      } catch (e) {
        console.error(`run-health check FAILED to run for run ${runId} (non-fatal): ${String(e)}`);
        return Promise.resolve(null);
      } finally {
        db?.close();
      }
    },

    alert: async (req: AlertRequest): Promise<void> => {
      const { attempt, key } = activityInfo();
      await sendAlert(req, { env: deps.env, send, attempt, maxAttempts: deps.maxAttempts, ...(key ? { idempotencyKey: key } : {}) });
    },
  };
}
