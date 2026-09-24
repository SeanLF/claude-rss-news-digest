import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { htmlEscape } from "escape-goat";
import { Hono, type Context } from "hono";

// A stand-in for the Resend API on the dev stack (resend-fake in docker-compose.yml): the slice the
// worker and the site call, recorded and shown at /. Nothing leaves the container. Adopted
// nothing because nothing maintained fakes broadcasts or segments (resend-box fakes POST /emails only).

export interface Message {
  id: string;
  kind: "email" | "broadcast";
  createdAt: string;
  from: string;
  to: string[]; // a broadcast's: the segment's subscribed contacts when it was sent
  subject: string;
  html: string;
  text?: string;
  replyTo?: string[];
  // broadcasts only
  name?: string;
  segmentId?: string;
  status?: "draft" | "sent";
  sentAt?: string | null;
}
export interface Contact {
  id: string;
  email: string;
  unsubscribed: boolean;
  created_at: string;
}
export interface ResendFake {
  app: Hono;
  messages: () => Message[];
  contacts: (segmentId: string) => Contact[];
  reset: () => void;
}

type Body = Record<string, unknown>;
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : typeof v === "string" && v ? [v] : []);
const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
// Resend's error body; the SDK hands it back as `{ error }`.
const fail = (statusCode: 401 | 404 | 422, name: string, message: string): Response => Response.json({ statusCode, name, message }, { status: statusCode });
const body = async (c: Context): Promise<Body> => ((await c.req.json().catch(() => ({}))) as Body | null) ?? {};
const page = (title: string, content: string) =>
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${htmlEscape(title)}</title>` +
  `<style>body{font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;color:#1a1a1a;background:#fff}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}iframe{width:100%;height:80vh;border:1px solid #ccc}</style></head><body>${content}</body></html>`;

interface State {
  messages: Message[];
  segments: Record<string, Contact[]>;
  idempotent: Record<string, string>;
}
// A file that is there but unreadable stops the fake: starting empty would write over the audience.
function load(file: string): State | undefined {
  if (!existsSync(file)) return undefined;
  try {
    const s = JSON.parse(readFileSync(file, "utf8")) as Partial<State>;
    if (!Array.isArray(s.messages) || !s.segments || typeof s.segments !== "object" || !s.idempotent || typeof s.idempotent !== "object") throw new Error("not a resend-fake state");
    return s as State;
  } catch (e) {
    throw new Error(`resend-fake cannot read ${file} (${String(e)}); remove it with: docker compose run --rm --no-deps resend-fake rm /data/state.json`, { cause: e });
  }
}

// With stateFile, every change is written through (whole file, then renamed over), so a restart keeps
// the dev audience and what was caught. Without it, memory only (the tests').
export function resendFake(opts: { now?: () => Date; stateFile?: string } = {}): ResendFake {
  const now = () => (opts.now ?? (() => new Date()))().toISOString();
  const kept = opts.stateFile ? load(opts.stateFile) : undefined;
  let messages: Message[] = kept?.messages ?? [];
  let segments = new Map<string, Contact[]>(Object.entries(kept?.segments ?? {}));
  let idempotent = new Map<string, string>(Object.entries(kept?.idempotent ?? {}));
  const save = () => {
    if (!opts.stateFile) return;
    const state: State = { messages, segments: Object.fromEntries(segments), idempotent: Object.fromEntries(idempotent) };
    writeFileSync(`${opts.stateFile}.tmp`, JSON.stringify(state));
    renameSync(`${opts.stateFile}.tmp`, opts.stateFile);
  };
  if (!kept) save(); // a state file that cannot be written fails the start, not the first subscribe
  const app = new Hono({ strict: false });
  const segment = (id: string): Contact[] => segments.get(id) ?? segments.set(id, []).get(id)!;
  const byId = (id: string) => messages.find((m) => m.id === id);

  // The API: every call carries a key, as Resend requires; any key will do.
  const api = new Hono();
  api.use("*", async (c, next) => {
    if (!/^Bearer \S+/.test(c.req.header("authorization") ?? "")) return fail(401, "missing_api_key", "Missing API key in the authorization header");
    return next();
  });
  api.post("/emails", async (c) => {
    const key = c.req.header("idempotency-key");
    const seen = key ? idempotent.get(key) : undefined;
    if (seen) return c.json({ id: seen });
    const b = await body(c);
    const [from, to, subject] = [str(b["from"]), list(b["to"]), str(b["subject"])];
    if (!from || !to.length || !subject || !(str(b["html"]) || str(b["text"]))) return fail(422, "validation_error", "from, to, subject and html or text are required");
    const m: Message = { id: randomUUID(), kind: "email", createdAt: now(), from, to, subject, html: str(b["html"]) ?? "", ...(str(b["text"]) ? { text: str(b["text"])! } : {}), ...(list(b["reply_to"]).length ? { replyTo: list(b["reply_to"]) } : {}) };
    messages.push(m);
    if (key) idempotent.set(key, m.id);
    save();
    return c.json({ id: m.id });
  });
  // The deprecated audience door the site uses; Resend upserts on the address.
  api.post("/audiences/:segment/contacts", async (c) => {
    const email = str((await body(c))["email"]);
    if (!email) return fail(422, "validation_error", "email is required");
    const contacts = segment(c.req.param("segment"));
    const found = contacts.find((x) => x.email === email);
    if (found) return c.json({ object: "contact", id: found.id });
    const contact: Contact = { id: randomUUID(), email, unsubscribed: false, created_at: now() };
    contacts.push(contact);
    save();
    return c.json({ object: "contact", id: contact.id }, 201);
  });
  api.get("/segments/:segment/contacts", (c) => {
    const contacts = segment(c.req.param("segment"));
    const limit = Math.max(1, Math.min(100, Number(c.req.query("limit") ?? 100)));
    const after = c.req.query("after");
    const start = after ? contacts.findIndex((x) => x.id === after) + 1 : 0;
    const data = contacts.slice(start, start + limit);
    return c.json({ object: "list", has_more: start + limit < contacts.length, data });
  });
  api.post("/broadcasts", async (c) => {
    const b = await body(c);
    const [from, segmentId, subject] = [str(b["from"]), str(b["segment_id"]) ?? str(b["audience_id"]), str(b["subject"])];
    if (!from || !segmentId || !subject) return fail(422, "validation_error", "from, segment_id and subject are required");
    const m: Message = { id: randomUUID(), kind: "broadcast", createdAt: now(), from, to: [], subject, html: str(b["html"]) ?? "", segmentId, status: "draft", sentAt: null, ...(str(b["name"]) ? { name: str(b["name"])! } : {}), ...(list(b["reply_to"]).length ? { replyTo: list(b["reply_to"]) } : {}) };
    messages.push(m);
    save();
    return c.json({ id: m.id }, 201);
  });
  api.post("/broadcasts/:id/send", (c) => {
    const m = byId(c.req.param("id"));
    if (!m || m.kind !== "broadcast") return fail(404, "not_found", "Broadcast not found");
    if (m.status === "sent") return fail(422, "validation_error", "Broadcast has already been sent");
    Object.assign(m, { status: "sent", sentAt: now(), to: segment(m.segmentId!).filter((x) => !x.unsubscribed).map((x) => x.email) });
    save();
    return c.json({ id: m.id });
  });
  api.get("/broadcasts/:id", (c) => {
    const m = byId(c.req.param("id"));
    if (!m || m.kind !== "broadcast") return fail(404, "not_found", "Broadcast not found");
    return c.json({ object: "broadcast", id: m.id, name: m.name ?? null, segment_id: m.segmentId, from: m.from, subject: m.subject, reply_to: m.replyTo ?? null, status: m.status, created_at: m.createdAt, scheduled_at: null, sent_at: m.sentAt });
  });
  // Anything else Resend has and this does not is a loud failure, never a quiet success.
  api.all("*", (c) => fail(404, "not_found", `resend-fake does not implement ${c.req.method} ${new URL(c.req.url).pathname}`));

  // The viewer. Message bodies render only inside a sandboxed frame, as a mail client would.
  app.get("/", (c) => {
    const rows = messages
      .toReversed()
      .map((m) => `<tr><td>${htmlEscape(m.createdAt.slice(0, 19))}</td><td>${m.kind}${m.status ? ` (${m.status})` : ""}</td><td>${htmlEscape(m.to.join(", ") || (m.segmentId ? `segment ${m.segmentId}` : ""))}</td><td><a href="/view/${m.id}">${htmlEscape(m.subject)}</a></td></tr>`)
      .join("");
    return c.html(page("resend-fake", `<h1>resend-fake</h1><p>${messages.length} caught; nothing is delivered. Contacts: ${[...segments].map(([id, xs]) => `${htmlEscape(id)} ${xs.length}`).join(", ") || "none"}. ${opts.stateFile ? "Kept across restarts; <code>make dev-mail-clear</code> empties it." : "In memory."}</p><table><tr><th>at</th><th>kind</th><th>to</th><th>subject</th></tr>${rows}</table>`));
  });
  app.get("/view/:id", (c) => {
    const m = byId(c.req.param("id"));
    if (!m) return c.notFound();
    const head = [["from", m.from], ["to", m.to.join(", ")], ["subject", m.subject], ["kind", `${m.kind}${m.status ? ` (${m.status})` : ""}`], ["reply-to", m.replyTo?.join(", ") ?? ""]]
      .map(([k, v]) => `<tr><th>${k}</th><td>${htmlEscape(v ?? "")}</td></tr>`)
      .join("");
    return c.html(page(m.subject, `<p><a href="/">all messages</a></p><table>${head}</table><iframe sandbox src="/view/${m.id}/body" title="message body"></iframe>`));
  });
  app.get("/view/:id/body", (c) => {
    const m = byId(c.req.param("id"));
    if (!m) return c.notFound();
    return c.body(m.html || htmlEscape(m.text ?? ""), 200, { "content-type": "text/html; charset=utf-8", "content-security-policy": "sandbox" });
  });
  app.get("/api/messages", (c) => c.json(messages));
  // make dev-mail-clear: messages, contacts and idempotency keys, all of it. The header forces a CORS
  // preflight, so a page open in a local browser cannot wipe the dev audience with a plain form POST.
  app.post("/api/reset", (c) => {
    if (c.req.header("x-resend-fake-reset") !== "yes") return c.json({ error: "send x-resend-fake-reset: yes" }, 400);
    const cleared = { messages: messages.length, contacts: [...segments.values()].reduce((n, xs) => n + xs.length, 0) };
    reset();
    return c.json({ cleared });
  });
  app.get("/health", (c) => c.text("ok"));
  app.route("/", api);

  function reset() {
    messages = [];
    segments = new Map();
    idempotent = new Map();
    save();
  }
  return { app, messages: () => messages, contacts: (id) => segment(id), reset };
}
