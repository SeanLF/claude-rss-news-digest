import { htmlEscape } from "escape-goat";
import type { SendEmail } from "../mail/resend.js";

// The operational alerts broadcast.py sends, to the same address (HEALTH_ALERT_EMAIL), plus
// run-failed: the systemd OnFailure alert's successor, raised by the workflow itself.
export type AlertRequest =
  | { kind: "run-health"; runId: number; violations: string[] }
  | { kind: "archival"; runId: number | null; failed: string[] }
  | { kind: "source-health"; failing: [sourceId: string, consecutive: number][]; failedThisRun: number; totalSources: number; threshold: number }
  | { kind: "run-failed"; workflowId: string; runId: number | null; reason: string; timedOut: boolean };

const FOOTER = '<p style="color: #777; font-size: 0.85em;">This is an automated alert from your News Digest system.</p>\n';

// `dropped` is the alert in one line: what the log keeps when the email cannot be sent.
export function alertEmail(req: AlertRequest): { subject: string; html: string; dropped: string } {
  switch (req.kind) {
    case "run-health": {
      const listed = req.violations.map((v) => `  • ${htmlEscape(v)}`).join("\n");
      return {
        subject: `[Alert] Run ${req.runId} completed but violated ${req.violations.length} invariant(s)`,
        html: `<h2>News Digest Run Health Alert</h2>
<p>Run <strong>${req.runId}</strong> completed and exited cleanly, but did not pass its
post-run checks:</p>
<pre>${listed}</pre>
<p>Nothing crashed -- this is the silent-failure class, so the run looks healthy in
the logs. Start with <code>bin/analytics run run-reliability</code>.</p>
${FOOTER}`,
        dropped: `run ${req.runId} violated: ${req.violations.join("; ")}`,
      };
    }
    case "archival": {
      const steps = req.failed.join(", ");
      return {
        subject: `[Alert] Digest archival failed (${steps})`,
        html: `<h2>News Digest Archival Alert</h2>
<p>Trace/analytics archival failed for <strong>${htmlEscape(steps)}</strong> on run ${req.runId ?? "None"}.</p>
<p>The digest still delivered (archival is fail-soft), but this run's reproducibility
trace is incomplete. If this recurs, the eval golden set is silently rotting -- check
the DB volume (disk/permissions/locks).</p>
${FOOTER}`,
        dropped: `archival failed for ${steps} on run ${req.runId ?? "None"}`,
      };
    }
    case "source-health": {
      const listed = req.failing.map(([id, n]) => `  • ${htmlEscape(id)}: ${n} consecutive failures`).join("\n");
      return {
        subject: `[Alert] ${req.failing.length} RSS sources failing`,
        html: `<h2>News Digest Source Health Alert</h2>
<p><strong>${req.failedThisRun}/${req.totalSources}</strong> sources failed this run.</p>
<p>The following sources have failed ${req.threshold}+ times in a row:</p>
<pre>${listed}</pre>
<p>Consider checking these feeds or removing them from sources.json.</p>
${FOOTER}`,
        dropped: `${req.failedThisRun}/${req.totalSources} sources failed this run; persistently failing: ${req.failing.map(([id, n]) => `${id} (${n}x)`).join(", ")}`,
      };
    }
    case "run-failed": {
      const what = req.timedOut ? "timed out" : "failed";
      const run = req.runId === null ? "run not started" : `run ${req.runId}`;
      return {
        subject: `[Alert] ${req.workflowId} ${what} (${run})`,
        html: `<h2>News Digest Run ${req.timedOut ? "Timed Out" : "Failed"}</h2>
<p>Workflow <strong>${htmlEscape(req.workflowId)}</strong> (${run}) ${what}; today's digest was not sent.</p>
<pre>${htmlEscape(req.reason)}</pre>
<p>Its history is in the Temporal UI under that workflow id. A resume re-runs only what is missing:
<code>make digest-start DATE=... ARGS="--resume ${req.runId ?? "N"}"</code>.</p>
${FOOTER}`,
        dropped: `${req.workflowId} ${what} (${run}): ${req.reason}`,
      };
    }
    default: {
      const unknown: never = req;
      throw new Error(`no alert for ${JSON.stringify(unknown)}`);
    }
  }
}

export interface AlertDeps {
  env: Record<string, string | undefined>;
  send: SendEmail;
  attempt: number;
  maxAttempts: number;
  idempotencyKey?: string;
}

// Alerting is the monitor, so an alert that cannot be delivered is the one failure no alert can
// report: both undeliverable paths log at ERROR with the alert's content. A failed send throws while
// the activity has attempts left, so Temporal's retry policy is the retry.
export async function sendAlert(req: AlertRequest, deps: AlertDeps): Promise<"sent" | "dropped"> {
  const { subject, html, dropped } = alertEmail(req);
  const to = deps.env["HEALTH_ALERT_EMAIL"];
  const apiKey = deps.env["RESEND_API_KEY"];
  const from = deps.env["RESEND_FROM"];
  if (!to || !apiKey || !from) {
    const missing = Object.entries({ HEALTH_ALERT_EMAIL: to, RESEND_API_KEY: apiKey, RESEND_FROM: from }).filter(([, v]) => !v).map(([k]) => k);
    console.error(`ALERTING MISCONFIGURED (${missing.join("/")} unset): ${req.kind} alert DROPPED, not delivered. It said: ${dropped}`);
    return "dropped";
  }
  try {
    await deps.send({ from: `News Digest Alerts <${from}>`, to: [to], subject, html }, deps.idempotencyKey ? { idempotencyKey: deps.idempotencyKey } : undefined);
  } catch (e) {
    if (deps.attempt < deps.maxAttempts) throw e;
    console.error(`${req.kind} alert send FAILED (${String(e)}); alert DROPPED. It said: ${dropped}`);
    return "dropped";
  }
  console.log(JSON.stringify({ stage: "alert", kind: req.kind, sent: to }));
  return "sent";
}
