import { describe, expect, it, vi } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { openDb } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
import { broadcastActivities, type BroadcastDeps, type Mail } from "./broadcast.js";

type Call = [string, unknown];
const ok = <T>(data: T) => Promise.resolve({ data, error: null, headers: null });
const fail = (name: string, message = name) => Promise.resolve({ data: null, error: { name, message, statusCode: 500 }, headers: null });
type Reply = ReturnType<typeof ok> | ReturnType<typeof fail>;

// A stand-in for the Resend client: records every call, answers from per-method scripts (the last
// answer repeats), and lets a test look at the database at the moment of a call.
function fakeMail(script: { create?: (() => Reply)[]; send?: (() => Reply)[]; get?: (() => Reply)[]; contacts?: (() => Reply)[]; email?: (() => Reply)[] }, onSend?: () => void) {
  const calls: Call[] = [];
  const next = (name: keyof typeof script, fallback: () => Reply) => {
    const list = script[name] ?? [fallback];
    const n = calls.filter(([c]) => c === name).length;
    return (list[Math.min(n - 1, list.length - 1)] ?? fallback)();
  };
  const on = (name: keyof typeof script, arg: unknown, fallback: () => Reply) => {
    calls.push([name, arg]);
    if (name === "send") onSend?.();
    return next(name, fallback);
  };
  const mail = {
    broadcasts: {
      create: (p: unknown) => on("create", p, () => ok({ id: "b-new" })),
      send: (id: string) => on("send", id, () => ok({ id })),
      get: (id: string) => on("get", id, () => ok({ id, status: "draft" })),
    },
    contacts: { list: (p: unknown) => on("contacts", p, () => ok({ object: "list", has_more: false, data: [{ id: "c1", unsubscribed: false }, { id: "c2", unsubscribed: true }, { id: "c3", unsubscribed: false }] })) },
    emails: { send: (p: unknown) => on("email", p, () => ok({ id: "e1" })) },
  } as unknown as Mail;
  return { mail, calls, names: () => calls.map(([c]) => c) };
}

const page = (ids: string[], more: boolean) => () => ok({ object: "list", has_more: more, data: ids.map((id) => ({ id, unsubscribed: id.startsWith("u") })) });
const ENV = { BROADCAST_ENABLED: "true", RESEND_API_KEY: "re_test", RESEND_FROM: "digest@news.test", RESEND_AUDIENCE_ID: "aud-1", DIGEST_NAME: "Sean's Daily News Digest", CONTACT_EMAIL: "hello@news.test", HEALTH_ALERT_EMAIL: "ops@news.test", TEMPORAL_UI_URL: "http://digest-box:8233" };

// `row`: the day's issue is published; with an id or a status, the day's send is in that state too.
async function setup(row?: { id?: string; status?: string; recipients?: number }, env: Record<string, string> = ENV) {
  const url = await migratedDb([{ id: 300, runAt: "2026-09-08 10:25:40" }]);
  const db = openDb(url);
  if (row) await db.run("INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-08', 1, 300, '<html></html>')");
  if (row?.id || row?.status)
    await db.run("INSERT INTO sends (issue_date, run_id, revision, resend_id, status, recipients, claim_token, claimed_at) VALUES ('2026-09-08', 300, 1, $1, $2, $3, '00000000-0000-4000-8000-000000000001', now())", [row.id ?? null, row.status ?? "claimed", row.recipients ?? null]);
  const store = new ArtifactStore(url);
  const email = await store.put(300, "email.html", "<mjml-rendered>issue</mjml-rendered>");
  const selections = await store.put(300, "selections.json", JSON.stringify({ must_know: [{ headline: "Deal <signed>", sources: [] }], should_know: [{ headline: "Yen falls", sources: [] }] }));
  // The day's send as the tests read it: no row reads as nothing claimed.
  const state = async () => (await db.one("SELECT resend_id AS id, status, recipients FROM sends WHERE issue_date='2026-09-08'")) ?? { id: null, status: null, recipients: null };
  const make = (mail: Mail, extra: Partial<BroadcastDeps> = {}) => broadcastActivities({ store, dbUrl: url, mail: () => mail, env, retryDelayMs: 0, execution: () => ({ namespace: "default", workflowId: "digest-2026-09-08", runId: "r-123" }), ...extra });
  return { email, selections, state, make, url, db };
}

// An earlier run (120) with the given start and outcome and its issue, then this run (300) on
// 2026-09-08 with that day's next revision, and no send recorded for either.
async function earlierRun(earlier: { runAt: string; outcome: string }) {
  const url = await migratedDb([{ id: 120, runAt: earlier.runAt }, { id: 300, runAt: "2026-09-08 15:00:00" }]);
  const db = openDb(url);
  await db.run("UPDATE runs SET status = 'completed', outcome = $1 WHERE id = 120", [earlier.outcome]);
  await db.run("INSERT INTO issues (issue_date, revision, run_id, html) VALUES ((SELECT (started_at AT TIME ZONE 'UTC')::date FROM runs WHERE id = 120), 1, 120, '')");
  await db.run("INSERT INTO issues (issue_date, revision, run_id, html) SELECT '2026-09-08', COALESCE(max(revision), 0) + 1, 300, '' FROM issues WHERE issue_date = '2026-09-08'");
  const store = new ArtifactStore(url);
  const email = await store.put(300, "email.html", "<mjml-rendered>issue</mjml-rendered>");
  const fake = fakeMail({});
  const acts = broadcastActivities({ store, dbUrl: url, mail: () => fake.mail, env: ENV, retryDelayMs: 0 });
  return { db, fake, send: () => acts.broadcast(300, email) };
}

describe("broadcast: at most once per digest date (the 2026-06-16 rule)", () => {
  it("fresh: creates a draft for the audience, persists its id before the send, then records the send", async () => {
    const { email, state, make } = await setup({});
    let atSend: unknown;
    const fake = fakeMail({}, () => {
      atSend = state();
    });
    const out = await make(fake.mail).broadcast(300, email);
    expect(fake.names()).toEqual(["contacts", "create", "send"]);
    expect(fake.calls[1]![1]).toEqual({ from: "Sean's Daily News Digest <digest@news.test>", segmentId: "aud-1", subject: "Sean's Daily News Digest – September 08, 2026", html: "<mjml-rendered>issue</mjml-rendered>", name: "Digest September 08, 2026", replyTo: "hello@news.test" });
    expect(await atSend).toEqual({ id: "b-new", status: "draft", recipients: null });
    expect(await state()).toEqual({ id: "b-new", status: "sent", recipients: 2 });
    expect(out).toEqual({ broadcastId: "b-new", status: "sent", recipients: 2 });
  });
  it.each(["queued", "sending", "sent"])("accepted (%s): an already accepted broadcast is never touched again", async (status) => {
    const { email, state, make } = await setup({ id: "b-old", status, recipients: 11 });
    const fake = fakeMail({});
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status, recipients: 11 });
    expect(fake.names()).toEqual([]);
    expect(await state()).toEqual({ id: "b-old", status, recipients: 11 });
  });
  it("a draft, not accepted: re-probes, and re-sends the same draft only when it never went out", async () => {
    const { email, state, make } = await setup({ id: "b-old", status: "draft" });
    const fake = fakeMail({ get: [() => ok({ id: "b-old", status: "draft" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status: "sent", recipients: 0 });
    expect(fake.calls).toEqual([["get", "b-old"], ["send", "b-old"]]);
    expect(await state()).toEqual({ id: "b-old", status: "sent", recipients: null });
  });
  it("a draft, and the probe finds it accepted: records the status and does not send", async () => {
    const { email, state, make } = await setup({ id: "b-old", status: "draft" });
    const fake = fakeMail({ get: [() => ok({ id: "b-old", status: "queued" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status: "queued", recipients: 0 });
    expect(fake.names()).toEqual(["get"]);
    expect(await state()).toMatchObject({ id: "b-old", status: "queued" });
  });
  it("a send that fails after creation leaves the draft's id for the next attempt, which sends that draft instead of a new one", async () => {
    const { email, state, make } = await setup({});
    const down = fakeMail({ send: [() => fail("application_error", "read timeout")], get: [() => ok({ id: "b-new", status: "draft" })] });
    await expect(make(down.mail).broadcast(300, email)).rejects.toThrow(/read timeout/);
    expect(down.names()).toEqual(["contacts", "create", "send", "get"]);
    expect(await state()).toEqual({ id: "b-new", status: "draft", recipients: null });
    const up = fakeMail({ get: [() => ok({ id: "b-new", status: "draft" })] });
    expect(await make(up.mail).broadcast(300, email)).toMatchObject({ broadcastId: "b-new", status: "sent" });
    expect(up.names()).toEqual(["get", "send"]);
  });
  it("a send whose response fails but which Resend accepted counts as delivered", async () => {
    const { email, state, make } = await setup({});
    const fake = fakeMail({ send: [() => fail("application_error", "read timeout")], get: [() => ok({ id: "b-new", status: "queued" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-new", status: "queued", recipients: 2 });
    expect(await state()).toEqual({ id: "b-new", status: "queued", recipients: 2 });
  });
  it("a failed create sends nothing and records nothing", async () => {
    const { email, state, make } = await setup({});
    const fake = fakeMail({ create: [() => fail("validation_error", "bad from")] });
    await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/bad from/);
    expect(fake.names()).toEqual(["contacts", "create"]);
    expect(await state()).toEqual({ id: null, status: null, recipients: null });
  });
  // The claim names the sending run and the revision it mails, so a forced re-run that publishes a
  // later revision leaves the record of who sent what.
  it("records the sending run and the mailed revision with the claim, and keeps them when a later revision is published", async () => {
    const { email, make, db } = await setup({});
    let atCreate: unknown;
    const fake = fakeMail({
      create: [
        () => {
          atCreate = db.one("SELECT run_id, revision, status FROM sends");
          return ok({ id: "b-new" });
        },
      ],
    });
    await make(fake.mail).broadcast(300, email);
    expect(await atCreate).toEqual({ run_id: 300, revision: 1, status: "claimed" });
    await db.exec("INSERT INTO runs (id, started_at) VALUES (301, '2026-09-08 14:00:00'); INSERT INTO issues (issue_date, revision, run_id, html) VALUES ('2026-09-08', 2, 301, '')");
    expect(await db.one("SELECT run_id, revision, status FROM sends")).toEqual({ run_id: 300, revision: 1, status: "sent" });
  });
  it("unless BROADCAST_ENABLED is true it says so and refuses to send, never calling Resend", async () => {
    for (const flag of [undefined, "", "false", "1", "yes"]) {
      const { BROADCAST_ENABLED: _on, ...rest } = ENV;
      const { email, state, make } = await setup({}, flag === undefined ? rest : { ...rest, BROADCAST_ENABLED: flag });
      const fake = fakeMail({});
      expect(await make(fake.mail).sendEnabled()).toBe(false);
      await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/BROADCAST_ENABLED/);
      expect(fake.names()).toEqual([]);
      expect(await state()).toEqual({ id: null, status: null, recipients: null });
    }
  });
  it("two sends for the same date at once: one claims the date and sends, the other sends nothing", async () => {
    const { email, state, make } = await setup({});
    const fake = fakeMail({});
    const results = await Promise.allSettled([make(fake.mail).broadcast(300, email), make(fake.mail).broadcast(300, email)]);
    expect(fake.names().filter((n) => n === "create")).toHaveLength(1);
    expect(fake.names().filter((n) => n === "send")).toHaveLength(1);
    expect(results.map((r) => r.status).toSorted()).toEqual(["fulfilled", "rejected"]);
    expect(String((results.find((r) => r.status === "rejected") as PromiseRejectedResult).reason)).toMatch(/claimed/);
    expect(await state()).toEqual({ id: "b-new", status: "sent", recipients: 2 });
  });
  it("a claim of any age holds the date: none is taken over, and the refusal names the command that clears it", async () => {
    for (const at of [new Date(), new Date(Date.now() - 24 * 60 * 60 * 1000)]) {
      const s = await setup({ status: "claimed" });
      await s.db.run("UPDATE sends SET claimed_at = $1", [at.toISOString()]);
      const fake = fakeMail({});
      await expect(s.make(fake.mail).broadcast(300, s.email)).rejects.toThrow(/claimed.*node dist\/cli\/clear-claim\.js 2026-09-08/);
      expect(fake.names().filter((n) => n === "create" || n === "send")).toEqual([]);
    }
  });
  it("heartbeats on every page of the audience count, and stops when cancelled mid-count", async () => {
    const { email, make } = await setup({});
    const beats: number[] = [];
    const ac = new AbortController();
    const pages = [page(["c1"], true), page(["c2"], true), page(["c3"], false)];
    const fake = fakeMail({ contacts: pages });
    await make(fake.mail, { heartbeat: () => beats.push(1) }).broadcast(300, email);
    expect(beats.length).toBeGreaterThanOrEqual(3 + 2); // one per page, and the send's own checks
    const stopped = fakeMail({
      contacts: [
        () => {
          ac.abort();
          return page(["c1"], true)();
        },
      ],
    });
    const fresh = await setup({});
    await expect(fresh.make(stopped.mail, { signal: () => ac.signal }).broadcast(300, fresh.email)).rejects.toThrow();
    expect(stopped.names()).toEqual(["contacts"]);
  });
  it("takes the claim only after counting the audience, the slow part", async () => {
    const { email, state, make } = await setup({});
    let atCount: unknown;
    const fake = fakeMail({ contacts: [
        () => {
          atCount = state();
          return page(["c1"], false)();
        },
      ] });
    await make(fake.mail).broadcast(300, email);
    expect(await atCount).toMatchObject({ status: null });
  });
  it("an attempt hung in create past any timeout, then a second attempt: exactly one send", async () => {
    const { email, state, make } = await setup({});
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slow = fakeMail({ create: [() => gate.then(() => ok({ id: "b-a" })) as Reply] });
    const a = make(slow.mail).broadcast(300, email);
    await new Promise((r) => setTimeout(r, 20)); // A holds the claim and waits in create
    vi.useFakeTimers({ toFake: ["Date"], now: Date.now() + 16 * 60 * 1000 }); // past any attempt's lifetime
    const other = fakeMail({});
    try {
      await expect(make(other.mail).broadcast(300, email)).rejects.toThrow(/claimed/);
    } finally {
      vi.useRealTimers();
    }
    release();
    await a;
    expect([...slow.names(), ...other.names()].filter((n) => n === "send")).toHaveLength(1);
    expect(await state()).toMatchObject({ id: "b-a", status: "sent" });
  });
  it("an attempt whose claim was cleared while it hung in create records nothing and sends nothing", async () => {
    const { email, state, make, db } = await setup({});
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slow = fakeMail({ create: [() => gate.then(() => ok({ id: "b-a" })) as Reply] });
    const a = make(slow.mail).broadcast(300, email).catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 20));
    await db.run("DELETE FROM sends WHERE issue_date='2026-09-08'"); // the operator clears it
    const b = fakeMail({ create: [() => ok({ id: "b-b" })] });
    await make(b.mail).broadcast(300, email);
    release();
    expect(String(await a)).toMatch(/claim/);
    expect([...slow.names(), ...b.names()].filter((n) => n === "send")).toHaveLength(1);
    expect(await state()).toMatchObject({ id: "b-b", status: "sent" });
  });
  it("a cancelled attempt stops before sending, and gives the claim back", async () => {
    const { email, state, make } = await setup({});
    const ac = new AbortController();
    const fake = fakeMail({ create: [
        () => {
          ac.abort();
          return ok({ id: "b-new" });
        },
      ] });
    await expect(make(fake.mail, { signal: () => ac.signal }).broadcast(300, email)).rejects.toThrow();
    expect(fake.names()).not.toContain("send");
    expect(await state()).toMatchObject({ id: null, status: null });
  });
  // An imported day from before broadcasts: its run emailed readers (Resend transactional mail, counted
  // in Resend) and the import gives it no sends row. A forced re-run publishes a new revision and must
  // not email the day again.
  it("refuses a day an earlier run already emailed, though the day has no send recorded", async () => {
    const { db, fake, send } = await earlierRun({ runAt: "2026-09-08 00:00:00", outcome: "sent" });
    await expect(send()).rejects.toThrow(/run 120 already emailed 2026-09-08/);
    expect(fake.names().filter((n) => n !== "contacts")).toEqual([]);
    expect(await db.all("SELECT * FROM sends")).toEqual([]);
  });
  // Controls: the refusal is the day's, and needs evidence of an email.
  it.each([
    ["the day before, a second before midnight UTC", { runAt: "2026-09-07 23:59:59", outcome: "sent" }],
    ["the same day, unrecorded (Python completed it, nothing says it emailed)", { runAt: "2026-09-08 10:25:40", outcome: "unrecorded" }],
  ])("sends past an earlier run %s", async (_name, earlier) => {
    const { fake, send } = await earlierRun(earlier);
    expect(await send()).toMatchObject({ status: "sent" });
    expect(fake.names()).toEqual(["contacts", "create", "send"]);
  });
  it("refuses to send a digest that was never published: the send mails a published revision", async () => {
    const { email, make } = await setup();
    const fake = fakeMail({});
    await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/no issue for 2026-09-08/);
    expect(fake.names()).toEqual([]);
  });
  it("retries a rate-limited call, which Resend did not accept", async () => {
    const { email, make } = await setup({});
    const fake = fakeMail({ create: [() => fail("rate_limit_exceeded"), () => fail("rate_limit_exceeded"), () => ok({ id: "b-new" })] });
    expect(await make(fake.mail).broadcast(300, email)).toMatchObject({ broadcastId: "b-new", status: "sent" });
    expect(fake.names()).toEqual(["contacts", "create", "create", "create", "send"]);
  });
  it("counts subscribed contacts across every page of the audience", async () => {
    const { email, make } = await setup({});
    const fake = fakeMail({ contacts: [page(["c1", "u2"], true), page(["c3"], false)] });
    expect(await make(fake.mail).broadcast(300, email)).toMatchObject({ recipients: 2 });
    expect(fake.calls.filter(([c]) => c === "contacts").map(([, p]) => p)).toEqual([{ segmentId: "aud-1", limit: 100 }, { segmentId: "aud-1", limit: 100, after: "u2" }]);
  });
});

describe("notifyHold", () => {
  const FAILED = ["INTERNAL_ID_LEAK: 1 leak(s): must_know.summary '(A2)' in 'Deal <signed>'", "THREAD_AUDIT_FAILED: 1 thread update(s) shipped facts their audit could not check (it fails open)"];
  it("emails the operator every failed check, when the hold ends and that it then sends, the signals, the run's link and its headlines", async () => {
    const { selections, make } = await setup({});
    const fake = fakeMail({});
    expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z", FAILED)).toEqual({ sent: true });
    expect(fake.names()).toEqual(["email"]);
    const p = fake.calls[0]![1] as { from: string; to: string[]; subject: string; html: string };
    expect(p.from).toBe("News Digest Alerts <digest@news.test>");
    expect(p.to).toEqual(["ops@news.test"]);
    expect(p.subject).toBe("[Hold] Digest 2026-09-08: INTERNAL_ID_LEAK, THREAD_AUDIT_FAILED; sends at 12:45 UTC unless rejected");
    expect(p.html).toContain("<li>INTERNAL_ID_LEAK: 1 leak(s): must_know.summary &#39;(A2)&#39; in &#39;Deal &lt;signed&gt;&#39;</li>");
    expect(p.html).toContain("<li>THREAD_AUDIT_FAILED: 1 thread update(s)");
    expect(p.html).toContain("sends anyway");
    expect(p.html).toContain("--name approve --input &#39;{&quot;decision&quot;:&quot;reject&quot;}&#39;");
    expect(p.html).toContain("http://digest-box:8233/namespaces/default/workflows/digest-2026-09-08/r-123/history");
    expect(p.html).toContain("Deal &lt;signed&gt;");
    expect(p.html).toContain("Yen falls");
    expect(p.html).toContain("12:45");
  });
  it("without an operator address it sends nothing and logs what it would have said, rather than failing the run", async () => {
    const { selections, make } = await setup({}, { ...ENV, HEALTH_ALERT_EMAIL: "" });
    const fake = fakeMail({});
    const logged: string[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((m: string) => void logged.push(m));
    try {
      expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z", FAILED)).toEqual({ sent: false });
    } finally {
      spy.mockRestore();
    }
    expect(fake.names()).toEqual([]);
    expect(logged.join("\n")).toContain("INTERNAL_ID_LEAK");
  });
  it("a Resend error is reported, not thrown", async () => {
    const { selections, make } = await setup({});
    const fake = fakeMail({ email: [() => fail("application_error")] });
    expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z", FAILED)).toEqual({ sent: false });
  });
});
