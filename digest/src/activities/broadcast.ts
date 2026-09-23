import { Context } from "@temporalio/activity";
import { ApplicationFailure } from "@temporalio/common";
import { htmlEscape } from "escape-goat";
import pRetry from "p-retry";
import type { ErrorResponse, Resend } from "resend";
import type { Selections } from "../render/render.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { openDb } from "../store/db.js";

// The slice of the Resend client the send uses; tests pass a fake with the same shape.
export interface Mail {
  broadcasts: Pick<Resend["broadcasts"], "create" | "send" | "get">;
  contacts: Pick<Resend["contacts"], "list">;
  emails: Pick<Resend["emails"], "send">;
}

// A broadcast in any of these states was accepted for delivery, so a send whose response failed
// actually landed. "sent" is safe only because a first send always goes to a fresh draft.
export const ACCEPTED_BROADCAST_STATES: ReadonlySet<string> = new Set(["queued", "sending", "sent"]);
// The send activity's start-to-close is 10 minutes; a claim older than this outlived its attempt.
export const CLAIM_TTL_MS = 15 * 60 * 1000;
const CLAIMED = "claimed ";
export const CONTACT_THRESHOLD = 900; // Resend's free tier stops at 1,000 contacts (spec §3)

interface Execution { namespace: string; workflowId: string; runId: string }
const currentExecution = (): Execution => {
  const { info } = Context.current();
  const ex = info.workflowExecution;
  if (!ex) throw ApplicationFailure.nonRetryable("the hold notification links a workflow run, and this activity has none", "NoWorkflow");
  return { namespace: info.namespace, workflowId: ex.workflowId, runId: ex.runId };
};
export interface BroadcastDeps {
  store: ArtifactStore;
  dbPath: string;
  mail: () => Mail; // lazy: the Resend client refuses to construct without a key
  env: NodeJS.ProcessEnv;
  retryDelayMs?: number;
  execution?: () => Execution;
}
export interface BroadcastResult { broadcastId: string; status: string; recipients: number }

class RateLimited extends Error {}
class ResendFailure extends Error {
  constructor(readonly error: ErrorResponse) {
    super(`Resend ${error.name}: ${error.message}`);
  }
}
type Reply<T> = { data: T; error: null } | { data: null; error: ErrorResponse };

// "%B %d, %Y" of the run's UTC day, as the Python stamps the subject and the broadcast's name.
const longDate = (day: string): string => {
  const d = new Date(`${day}T00:00:00Z`);
  return `${d.toLocaleString("en-US", { month: "long", timeZone: "UTC" })} ${String(d.getUTCDate()).padStart(2, "0")}, ${d.getUTCFullYear()}`;
};

const headlineList = (tier: Selections["must_know"]) => tier.map((s) => `<li>${htmlEscape(s.headline ?? "")}</li>`).join("");

// broadcast.py and run._deliver: Resend audience broadcasts, idempotent per digest date through the
// broadcast columns on its digests row (the 2026-06-16 incident).
export function broadcastActivities(deps: BroadcastDeps) {
  const { store, env } = deps;
  // Resend's rate limit is a refusal, never an acceptance, so it alone is retried, as broadcast.py
  // retries RateLimitError; anything else comes back to the caller as the reply it is.
  const call = <T>(fn: () => Promise<Reply<T>>): Promise<Reply<T>> =>
    pRetry(
      async () => {
        const r = await fn();
        if (r.error?.name === "rate_limit_exceeded") throw new RateLimited(r.error.message);
        return r;
      },
      { retries: 2, minTimeout: deps.retryDelayMs ?? 1000, factor: 2, shouldRetry: ({ error }) => error instanceof RateLimited },
    );
  const must = async <T>(fn: () => Promise<Reply<T>>): Promise<T> => {
    const r = await call(fn);
    if (r.error) throw new ResendFailure(r.error);
    return r.data;
  };

  // A read failure throws: "cannot read the broadcast state" must never look like "nothing was sent".
  const readRow = (date: string) => {
    const db = openDb(deps.dbPath);
    try {
      return db.prepare("SELECT broadcast_id AS id, broadcast_status AS status, broadcast_recipients AS recipients FROM digests WHERE date=?").get(date) as { id: string | null; status: string | null; recipients: number | null } | undefined;
    } finally {
      db.close();
    }
  };
  const record = (date: string, id: string, status: string, recipients?: number) => {
    const db = openDb(deps.dbPath);
    try {
      const { changes } = db.prepare("UPDATE digests SET broadcast_id=?, broadcast_status=?, broadcast_recipients=COALESCE(?, broadcast_recipients) WHERE date=?").run(id, status, recipients ?? null, date);
      if (Number(changes) !== 1) throw new Error(`no digests row for ${date}; broadcast ${id} (${status}) was not recorded`);
    } finally {
      db.close();
    }
  };

  // The date's claim, taken under SQLite's write lock: the one attempt that holds it may create a
  // broadcast. A claim older than an attempt can live was left by one that died before creating
  // anything, so it is taken over rather than blocking the day.
  const claim = (date: string) => {
    const db = openDb(deps.dbPath);
    try {
      db.exec("BEGIN IMMEDIATE");
      try {
        const row = db.prepare("SELECT broadcast_id AS id, broadcast_status AS status FROM digests WHERE date=?").get(date) as { id: string | null; status: string | null } | undefined;
        const held = row?.status?.startsWith(CLAIMED) ? Date.parse(row.status.slice(CLAIMED.length)) : Number.NaN;
        if (!row || row.id !== null || (!Number.isNaN(held) && Date.now() - held < CLAIM_TTL_MS)) {
          throw ApplicationFailure.nonRetryable(`the send for ${date} is claimed by another attempt (${row?.id ?? row?.status ?? "no row"}); not sending`, "SendClaimed");
        }
        db.prepare("UPDATE digests SET broadcast_status=? WHERE date=?").run(`${CLAIMED}${new Date().toISOString()}`, date);
        db.exec("COMMIT");
      } catch (e) {
        db.exec("ROLLBACK");
        throw e;
      }
    } finally {
      db.close();
    }
  };
  const release = (date: string) => {
    const db = openDb(deps.dbPath);
    try {
      db.prepare("UPDATE digests SET broadcast_status=NULL WHERE date=? AND broadcast_id IS NULL AND broadcast_status LIKE ?").run(date, `${CLAIMED}%`);
    } finally {
      db.close();
    }
  };

  // Best-effort: a probe that cannot read the status answers null and the caller assumes nothing.
  const probe = async (id: string): Promise<string | null> => {
    try {
      const r = await call(() => deps.mail().broadcasts.get(id));
      if (r.error) {
        console.warn(JSON.stringify({ stage: "broadcast", warning: "could not read the broadcast's status", id, error: r.error.name }));
        return null;
      }
      return r.data.status;
    } catch (e) {
      console.warn(JSON.stringify({ stage: "broadcast", warning: "could not read the broadcast's status", id, error: String(e) }));
      return null;
    }
  };
  // broadcast.resend_existing: send a draft that already exists, never create one; a failed send
  // that Resend nonetheless accepted is a delivery.
  const sendExisting = async (id: string): Promise<string> => {
    try {
      await must(() => deps.mail().broadcasts.send(id));
      return "sent";
    } catch (e) {
      const status = await probe(id);
      if (status === null || !ACCEPTED_BROADCAST_STATES.has(status)) throw e;
      console.warn(JSON.stringify({ stage: "broadcast", warning: "the send failed but the broadcast was accepted; treating it as delivered", id, status, error: String(e) }));
      return status;
    }
  };
  const contactCount = async (segmentId: string): Promise<number> => {
    let count = 0;
    let after: string | undefined;
    do {
      const r = await call(() => deps.mail().contacts.list({ segmentId, limit: 100, ...(after ? { after } : {}) }));
      if (r.error) return 0; // informational only, as broadcast.get_audience_contact_count
      count += r.data.data.filter((c) => !c.unsubscribed).length;
      after = r.data.has_more ? r.data.data.at(-1)?.id : undefined;
    } while (after);
    return count;
  };
  const required = (name: string): string => {
    const v = env[name];
    if (!v) throw ApplicationFailure.nonRetryable(`${name} is not set`, "MisconfiguredSend");
    return v;
  };

  // Off unless asked for: a worker pointed at a copy of the database, or started for a test, must
  // never mail the audience.
  const enabled = () => env["BROADCAST_ENABLED"] === "true";

  return {
    sendEnabled: (): Promise<boolean> => Promise.resolve(enabled()),

    broadcast: async (runId: number, email: Pointer): Promise<BroadcastResult> => {
      const date = store.runDate(runId);
      // The workflow asks sendEnabled first; this refuses anyway, so no path mails the audience
      // from a worker that was not told to.
      if (!enabled()) throw ApplicationFailure.nonRetryable("BROADCAST_ENABLED is not true; nothing sent", "SendDisabled");
      const html = store.get(email);
      const row = readRow(date);
      if (!row) throw ApplicationFailure.nonRetryable(`no digests row for ${date}: the digest is saved before its send, and its row is the send's idempotency record`, "MissingDigest");
      const log = (event: string, extra: Record<string, unknown>) => console.log(JSON.stringify({ stage: "broadcast", runId, date, event, ...extra }));
      if (row.id && row.status && ACCEPTED_BROADCAST_STATES.has(row.status)) {
        log("skipped: already accepted", { id: row.id, status: row.status });
        return { broadcastId: row.id, status: row.status, recipients: row.recipients ?? 0 };
      }
      if (row.id) {
        // An earlier attempt created this draft and never recorded its delivery: re-probe, and
        // re-send the same draft only if it never went out.
        const status = await probe(row.id);
        if (status !== null && ACCEPTED_BROADCAST_STATES.has(status)) {
          record(date, row.id, status);
          log("recovered: already accepted", { id: row.id, status });
          return { broadcastId: row.id, status, recipients: row.recipients ?? 0 };
        }
        const sent = await sendExisting(row.id);
        record(date, row.id, sent);
        log("re-sent the existing draft", { id: row.id, status: sent });
        return { broadcastId: row.id, status: sent, recipients: 0 }; // the send API returns no count
      }
      const segmentId = required("RESEND_AUDIENCE_ID");
      const from = required("RESEND_FROM");
      const name = env["DIGEST_NAME"] || "News Digest";
      claim(date); // before any await: two workflows for one date both read no id above
      let id: string;
      let recipients: number;
      try {
        recipients = await contactCount(segmentId);
        if (recipients >= CONTACT_THRESHOLD) console.warn(JSON.stringify({ stage: "broadcast", warning: `audience at ${recipients} contacts, near the free tier's 1,000`, recipients }));
        const day = longDate(date);
        ({ id } = await must(() =>
          deps.mail().broadcasts.create({ from: `${name} <${from}>`, segmentId, subject: `${name} – ${day}`, html, name: `Digest ${day}`, ...(env["CONTACT_EMAIL"] ? { replyTo: env["CONTACT_EMAIL"] } : {}) }),
        ));
      } catch (e) {
        release(date); // no draft to recover, so the next attempt may create one
        throw e;
      }
      record(date, id, "created"); // before the send, so a send that fails leaves the id to recover
      const status = await sendExisting(id);
      record(date, id, status, recipients);
      log("sent", { id, status, recipients });
      return { broadcastId: id, status, recipients };
    },

    // The pre-broadcast hold's notification (spec §2.3): the run's Temporal UI link and its
    // headlines, to the operator's alert address. Best-effort: the hold proceeds without it.
    notifyHold: async (runId: number, selections: Pointer, holdEndsAt: string | null): Promise<{ sent: boolean }> => {
      const date = store.runDate(runId);
      const sel = JSON.parse(store.get(selections)) as Selections;
      const to = env["HEALTH_ALERT_EMAIL"];
      const from = env["RESEND_FROM"];
      const until = holdEndsAt?.slice(11, 16);
      const dropped = until ? `digest ${date} (run ${runId}) is held for approval until ${holdEndsAt}` : `digest ${date} (run ${runId}) sends now, unheld: the run's budget had no time left for a hold`;
      if (!to || !from || !env["RESEND_API_KEY"]) {
        console.error(JSON.stringify({ stage: "hold", error: "ALERTING MISCONFIGURED (HEALTH_ALERT_EMAIL, RESEND_FROM or RESEND_API_KEY unset): hold notification dropped", dropped }));
        return { sent: false };
      }
      const ex = (deps.execution ?? currentExecution)();
      const ui = (env["TEMPORAL_UI_URL"] || "http://127.0.0.1:8233").replace(/\/+$/, "");
      const link = `${ui}/namespaces/${encodeURIComponent(ex.namespace)}/workflows/${encodeURIComponent(ex.workflowId)}/${encodeURIComponent(ex.runId)}/history`;
      const signal = (decision: string) => `temporal workflow signal --workflow-id ${ex.workflowId} --name approve --input '{"decision":"${decision}"}'`;
      const head = until
        ? `<h2>Digest ${date} is waiting to send</h2>
<p>Run ${runId} is held until <strong>${until} UTC</strong>, then it sends on its own. <a href="${htmlEscape(link)}">Open the run in the Temporal UI</a>.</p>
<p>To stop it: <code>${htmlEscape(signal("reject"))}</code><br>To send now: <code>${htmlEscape(signal("approve"))}</code></p>`
        : `<h2>Digest ${date} is sending now, without a hold</h2>
<p>Run ${runId} used its time budget before the hold, so it was <strong>not held</strong> for approval. <a href="${htmlEscape(link)}">Open the run in the Temporal UI</a>.</p>`;
      const html = `${head}
<h3>Must know (${sel.must_know.length})</h3><ol>${headlineList(sel.must_know)}</ol>
<h3>Should know (${sel.should_know.length})</h3><ol>${headlineList(sel.should_know)}</ol>`;
      const n = sel.must_know.length + sel.should_know.length;
      const subject = until ? `[Hold] Digest ${date}: ${n} stories send at ${until} UTC` : `[No hold] Digest ${date}: ${n} stories sending now, unheld`;
      try {
        const r = await call(() => deps.mail().emails.send({ from: `News Digest Alerts <${from}>`, to: [to], subject, html }));
        if (r.error) throw new ResendFailure(r.error);
      } catch (e) {
        console.error(JSON.stringify({ stage: "hold", error: `hold notification send FAILED (${String(e)}); dropped`, dropped }));
        return { sent: false };
      }
      console.log(JSON.stringify({ stage: "hold", runId, event: "notified", until: holdEndsAt }));
      return { sent: true };
    },
  };
}
