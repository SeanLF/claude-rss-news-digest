import { DatabaseSync } from "node:sqlite";
import { describe, expect, it, vi } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { migratedDb } from "../store/migrated-db.js";
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

function setup(row?: { id?: string; status?: string; recipients?: number }, env: Record<string, string> = ENV) {
  const path = migratedDb([{ id: 300, runAt: "2026-09-08 10:25:40" }]);
  const db = new DatabaseSync(path);
  if (row) db.prepare("INSERT INTO digests (date, html, run_id, broadcast_id, broadcast_status, broadcast_recipients) VALUES ('2026-09-08', '<html></html>', 300, ?, ?, ?)").run(row.id ?? null, row.status ?? null, row.recipients ?? null);
  const store = new ArtifactStore(path);
  const email = store.put(300, "email.html", "<mjml-rendered>issue</mjml-rendered>");
  const selections = store.put(300, "selections.json", JSON.stringify({ must_know: [{ headline: "Deal <signed>", sources: [] }], should_know: [{ headline: "Yen falls", sources: [] }] }));
  const state = () => db.prepare("SELECT broadcast_id AS id, broadcast_status AS status, broadcast_recipients AS recipients FROM digests WHERE date='2026-09-08'").get();
  const make = (mail: Mail, extra: Partial<BroadcastDeps> = {}) => broadcastActivities({ store, dbPath: path, mail: () => mail, env, retryDelayMs: 0, execution: () => ({ namespace: "default", workflowId: "digest-2026-09-08", runId: "r-123" }), ...extra });
  return { email, selections, state, make, path };
}

describe("broadcast: at most once per digest date (the 2026-06-16 rule)", () => {
  it("fresh: creates a draft for the audience, persists its id before the send, then records the send", async () => {
    const { email, state, make } = setup({});
    let atSend: unknown;
    const fake = fakeMail({}, () => (atSend = state()));
    const out = await make(fake.mail).broadcast(300, email);
    expect(fake.names()).toEqual(["contacts", "create", "send"]);
    expect(fake.calls[1]![1]).toEqual({ from: "Sean's Daily News Digest <digest@news.test>", segmentId: "aud-1", subject: "Sean's Daily News Digest – September 08, 2026", html: "<mjml-rendered>issue</mjml-rendered>", name: "Digest September 08, 2026", replyTo: "hello@news.test" });
    expect(atSend).toEqual({ id: "b-new", status: "created", recipients: null });
    expect(state()).toEqual({ id: "b-new", status: "sent", recipients: 2 });
    expect(out).toEqual({ broadcastId: "b-new", status: "sent", recipients: 2 });
  });
  it.each(["queued", "sending", "sent"])("accepted (%s): an already accepted broadcast is never touched again", async (status) => {
    const { email, state, make } = setup({ id: "b-old", status, recipients: 11 });
    const fake = fakeMail({});
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status, recipients: 11 });
    expect(fake.names()).toEqual([]);
    expect(state()).toEqual({ id: "b-old", status, recipients: 11 });
  });
  it("created, not accepted: re-probes, and re-sends the same draft only when it never went out", async () => {
    const { email, state, make } = setup({ id: "b-old", status: "created" });
    const fake = fakeMail({ get: [() => ok({ id: "b-old", status: "draft" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status: "sent", recipients: 0 });
    expect(fake.calls).toEqual([["get", "b-old"], ["send", "b-old"]]);
    expect(state()).toEqual({ id: "b-old", status: "sent", recipients: null });
  });
  it("created, and the probe finds it accepted: records the status and does not send", async () => {
    const { email, state, make } = setup({ id: "b-old", status: "created" });
    const fake = fakeMail({ get: [() => ok({ id: "b-old", status: "queued" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-old", status: "queued", recipients: 0 });
    expect(fake.names()).toEqual(["get"]);
    expect(state()).toMatchObject({ id: "b-old", status: "queued" });
  });
  it("a send that fails after creation leaves the draft's id for the next attempt, which sends that draft instead of a new one", async () => {
    const { email, state, make } = setup({});
    const down = fakeMail({ send: [() => fail("application_error", "read timeout")], get: [() => ok({ id: "b-new", status: "draft" })] });
    await expect(make(down.mail).broadcast(300, email)).rejects.toThrow(/read timeout/);
    expect(down.names()).toEqual(["contacts", "create", "send", "get"]);
    expect(state()).toEqual({ id: "b-new", status: "created", recipients: null });
    const up = fakeMail({ get: [() => ok({ id: "b-new", status: "draft" })] });
    expect(await make(up.mail).broadcast(300, email)).toMatchObject({ broadcastId: "b-new", status: "sent" });
    expect(up.names()).toEqual(["get", "send"]);
  });
  it("a send whose response fails but which Resend accepted counts as delivered", async () => {
    const { email, state, make } = setup({});
    const fake = fakeMail({ send: [() => fail("application_error", "read timeout")], get: [() => ok({ id: "b-new", status: "queued" })] });
    expect(await make(fake.mail).broadcast(300, email)).toEqual({ broadcastId: "b-new", status: "queued", recipients: 2 });
    expect(state()).toEqual({ id: "b-new", status: "queued", recipients: 2 });
  });
  it("a failed create sends nothing and records nothing", async () => {
    const { email, state, make } = setup({});
    const fake = fakeMail({ create: [() => fail("validation_error", "bad from")] });
    await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/bad from/);
    expect(fake.names()).toEqual(["contacts", "create"]);
    expect(state()).toEqual({ id: null, status: null, recipients: null });
  });
  it("unless BROADCAST_ENABLED is true it says so and refuses to send, never calling Resend", async () => {
    for (const flag of [undefined, "", "false", "1", "yes"]) {
      const { BROADCAST_ENABLED: _on, ...rest } = ENV;
      const { email, state, make } = setup({}, flag === undefined ? rest : { ...rest, BROADCAST_ENABLED: flag });
      const fake = fakeMail({});
      expect(await make(fake.mail).sendEnabled()).toBe(false);
      await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/BROADCAST_ENABLED/);
      expect(fake.names()).toEqual([]);
      expect(state()).toEqual({ id: null, status: null, recipients: null });
    }
  });
  it("two sends for the same date at once: one claims the date and sends, the other sends nothing", async () => {
    const { email, state, make } = setup({});
    const fake = fakeMail({});
    const results = await Promise.allSettled([make(fake.mail).broadcast(300, email), make(fake.mail).broadcast(300, email)]);
    expect(fake.names().filter((n) => n === "create")).toHaveLength(1);
    expect(fake.names().filter((n) => n === "send")).toHaveLength(1);
    expect(results.map((r) => r.status).toSorted()).toEqual(["fulfilled", "rejected"]);
    expect(String((results.find((r) => r.status === "rejected") as PromiseRejectedResult).reason)).toMatch(/claimed/);
    expect(state()).toEqual({ id: "b-new", status: "sent", recipients: 2 });
  });
  it("a claim of any age holds the date: none is taken over, and the refusal names the command that clears it", async () => {
    for (const at of [new Date(), new Date(Date.now() - 24 * 60 * 60 * 1000)]) {
      const s = setup({ status: `claimed ${at.toISOString()} someone` });
      const fake = fakeMail({});
      await expect(s.make(fake.mail).broadcast(300, s.email)).rejects.toThrow(/claimed.*node dist\/cli\/clear-claim\.js 2026-09-08/);
      expect(fake.names().filter((n) => n === "create" || n === "send")).toEqual([]);
    }
  });
  it("heartbeats on every page of the audience count, and stops when cancelled mid-count", async () => {
    const { email, make } = setup({});
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
    const fresh = setup({});
    await expect(fresh.make(stopped.mail, { signal: () => ac.signal }).broadcast(300, fresh.email)).rejects.toThrow();
    expect(stopped.names()).toEqual(["contacts"]);
  });
  it("takes the claim only after counting the audience, the slow part", async () => {
    const { email, state, make } = setup({});
    let atCount: unknown;
    const fake = fakeMail({ contacts: [
        () => {
          atCount = state();
          return page(["c1"], false)();
        },
      ] });
    await make(fake.mail).broadcast(300, email);
    expect(atCount).toMatchObject({ status: null });
  });
  it("an attempt hung in create past any timeout, then a second attempt: exactly one send", async () => {
    const { email, state, make } = setup({});
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
    expect(state()).toMatchObject({ id: "b-a", status: "sent" });
  });
  it("an attempt whose claim was cleared while it hung in create records nothing and sends nothing", async () => {
    const { email, state, make, path } = setup({});
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slow = fakeMail({ create: [() => gate.then(() => ok({ id: "b-a" })) as Reply] });
    const a = make(slow.mail).broadcast(300, email).catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 20));
    const d = new DatabaseSync(path);
    d.prepare("UPDATE digests SET broadcast_status=NULL WHERE date='2026-09-08'").run(); // the operator clears it
    d.close();
    const b = fakeMail({ create: [() => ok({ id: "b-b" })] });
    await make(b.mail).broadcast(300, email);
    release();
    expect(String(await a)).toMatch(/claim/);
    expect([...slow.names(), ...b.names()].filter((n) => n === "send")).toHaveLength(1);
    expect(state()).toMatchObject({ id: "b-b", status: "sent" });
  });
  it("a cancelled attempt stops before sending, and gives the claim back", async () => {
    const { email, state, make } = setup({});
    const ac = new AbortController();
    const fake = fakeMail({ create: [
        () => {
          ac.abort();
          return ok({ id: "b-new" });
        },
      ] });
    await expect(make(fake.mail, { signal: () => ac.signal }).broadcast(300, email)).rejects.toThrow();
    expect(fake.names()).not.toContain("send");
    expect(state()).toMatchObject({ id: null, status: null });
  });
  it("refuses to send a digest that has no saved row, since the row is its idempotency record", async () => {
    const { email, make } = setup();
    const fake = fakeMail({});
    await expect(make(fake.mail).broadcast(300, email)).rejects.toThrow(/no digests row/);
    expect(fake.names()).toEqual([]);
  });
  it("retries a rate-limited call, which Resend did not accept", async () => {
    const { email, make } = setup({});
    const fake = fakeMail({ create: [() => fail("rate_limit_exceeded"), () => fail("rate_limit_exceeded"), () => ok({ id: "b-new" })] });
    expect(await make(fake.mail).broadcast(300, email)).toMatchObject({ broadcastId: "b-new", status: "sent" });
    expect(fake.names()).toEqual(["contacts", "create", "create", "create", "send"]);
  });
  it("counts subscribed contacts across every page of the audience", async () => {
    const { email, make } = setup({});
    const fake = fakeMail({ contacts: [page(["c1", "u2"], true), page(["c3"], false)] });
    expect(await make(fake.mail).broadcast(300, email)).toMatchObject({ recipients: 2 });
    expect(fake.calls.filter(([c]) => c === "contacts").map(([, p]) => p)).toEqual([{ segmentId: "aud-1", limit: 100 }, { segmentId: "aud-1", limit: 100, after: "u2" }]);
  });
});

describe("notifyHold", () => {
  it("emails the operator the run's Temporal UI link and its headlines", async () => {
    const { selections, make } = setup({});
    const fake = fakeMail({});
    expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z")).toEqual({ sent: true });
    expect(fake.names()).toEqual(["email"]);
    const p = fake.calls[0]![1] as { from: string; to: string[]; subject: string; html: string };
    expect(p.from).toBe("News Digest Alerts <digest@news.test>");
    expect(p.to).toEqual(["ops@news.test"]);
    expect(p.subject).toContain("2026-09-08");
    expect(p.html).toContain("http://digest-box:8233/namespaces/default/workflows/digest-2026-09-08/r-123/history");
    expect(p.html).toContain("Deal &lt;signed&gt;");
    expect(p.html).toContain("Yen falls");
    expect(p.html).toContain("12:45");
  });
  it("with no budget left for a hold, says the issue is sending now, unheld", async () => {
    const { selections, make } = setup({});
    const fake = fakeMail({});
    expect(await make(fake.mail).notifyHold(300, selections, null)).toEqual({ sent: true });
    const p = fake.calls[0]![1] as { subject: string; html: string };
    expect(p.subject).toContain("unheld");
    expect(p.html).toContain("not held");
    expect(p.html).not.toContain("To stop it");
  });
  it("without an operator address it sends nothing and says so, rather than failing the run", async () => {
    const { selections, make } = setup({}, { ...ENV, HEALTH_ALERT_EMAIL: "" });
    const fake = fakeMail({});
    expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z")).toEqual({ sent: false });
    expect(fake.names()).toEqual([]);
  });
  it("a Resend error is reported, not thrown", async () => {
    const { selections, make } = setup({});
    const fake = fakeMail({ email: [() => fail("application_error")] });
    expect(await make(fake.mail).notifyHold(300, selections, "2026-09-08T12:45:00.000Z")).toEqual({ sent: false });
  });
});
