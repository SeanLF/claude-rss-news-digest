import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { serve, type ServerType } from "@hono/node-server";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { resendClient } from "../mail/resend.js";
import { resendFake, type ResendFake } from "./fake.js";

// The fake is held to the calls the real code makes, through the real SDK over a real socket: if the
// SDK or our use of it drifts, these fail here rather than on the day dev mail silently stops.
const listen = async (f: ResendFake): Promise<{ server: ServerType; base: string }> => {
  const server = serve({ fetch: f.app.fetch, port: 0, hostname: "127.0.0.1" });
  await new Promise((r) => server.once("listening", r));
  return { server, base: `http://127.0.0.1:${(server.address() as AddressInfo).port}` };
};
let fake: ResendFake;
let server: ServerType;
let base: string;
beforeAll(async () => {
  fake = resendFake({ now: () => new Date("2026-09-24T10:00:00Z") });
  ({ server, base } = await listen(fake));
});
afterAll(() => {
  server.close();
});
beforeEach(() => {
  fake.reset();
});
const client = () => resendClient("re_dev_fake", {}, { RESEND_BASE_URL: base });

describe("resend-fake", () => {
  it("captures an email, and one idempotency key is one email", async () => {
    const mail = client();
    const a = await mail.emails.send({ from: "Alerts <a@dev.invalid>", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" }, { idempotencyKey: "k1" });
    const b = await mail.emails.send({ from: "Alerts <a@dev.invalid>", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" }, { idempotencyKey: "k1" });
    expect(a.error).toBeNull();
    expect(b.data?.id).toBe(a.data?.id);
    expect(fake.messages()).toMatchObject([{ kind: "email", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" }]);
  });

  it("answers a malformed send as Resend does, so the caller's error path runs", async () => {
    const r = await client().emails.send({ from: "a@dev.invalid", to: [], subject: "s", html: "h" });
    expect(r.error).toMatchObject({ name: "validation_error", statusCode: 422 });
    expect(fake.messages()).toEqual([]);
  });

  it("refuses a request without a key, as Resend does", async () => {
    const r = await fetch(`${base}/emails`, { method: "POST", body: "{}" });
    expect(r.status).toBe(401);
  });

  it("the broadcast path: count the segment, create a draft, send it, read its status", async () => {
    const mail = client();
    for (const email of ["a@example.com", "b@example.com", "c@example.com"]) await mail.contacts.create({ audienceId: "aud-dev", email });
    const page = await mail.contacts.list({ segmentId: "aud-dev", limit: 2 });
    expect(page.data?.has_more).toBe(true);
    const rest = await mail.contacts.list({ segmentId: "aud-dev", limit: 2, after: page.data!.data.at(-1)!.id });
    expect([...page.data!.data, ...rest.data!.data].map((c) => c.email)).toEqual(["a@example.com", "b@example.com", "c@example.com"]);
    expect(rest.data?.has_more).toBe(false);

    const created = await mail.broadcasts.create({ from: "News Digest <news@dev.invalid>", segmentId: "aud-dev", subject: "News Digest – September 24, 2026", html: "<p>issue</p>", name: "Digest September 24, 2026" });
    const id = created.data!.id;
    expect((await mail.broadcasts.get(id)).data?.status).toBe("draft");
    expect(fake.messages()).toMatchObject([{ kind: "broadcast", status: "draft", to: [] }]);
    expect((await mail.broadcasts.send(id)).error).toBeNull();
    expect((await mail.broadcasts.get(id)).data).toMatchObject({ id, status: "sent", segment_id: "aud-dev" });
    expect(fake.messages()).toMatchObject([{ kind: "broadcast", status: "sent", to: ["a@example.com", "b@example.com", "c@example.com"], subject: "News Digest – September 24, 2026" }]);
    expect((await mail.broadcasts.send(id)).error).toMatchObject({ statusCode: 422 }); // a sent broadcast is not sent again
  });

  it("answers an endpoint it does not fake with a Resend-shaped 404, never a silent success", async () => {
    const r = await client().domains.list();
    expect(r.error).toMatchObject({ name: "not_found", statusCode: 404 });
  });

  it("shows what it caught: a list, each message, and its body in a sandbox", async () => {
    const sent = await client().emails.send({ from: "a@dev.invalid", to: ["ops@dev.invalid"], subject: "Hold <b>", html: "<p>body</p>" });
    const id = sent.data!.id;
    const index = await (await fetch(`${base}/`)).text();
    expect(index).toContain("Hold &lt;b&gt;");
    expect(index).toContain(`/view/${id}`);
    const view = await (await fetch(`${base}/view/${id}`)).text();
    expect(view).toContain(`<iframe sandbox src="/view/${id}/body"`);
    const body = await fetch(`${base}/view/${id}/body`);
    expect(body.headers.get("content-security-policy")).toBe("sandbox");
    expect(await body.text()).toBe("<p>body</p>");
    expect(await (await fetch(`${base}/api/messages`)).json()).toMatchObject([{ id, subject: "Hold <b>" }]);
  });
});

// The dev audience outlives the container: make dev-import and make dev-up restart it, and a restart
// that emptied it sent the next dev broadcast to nobody (ZERO_RECIPIENTS, 2026-09-24).
const stateFile = () => join(mkdtempSync(join(tmpdir(), "resend-fake-")), "state.json");
// One start of the fake over a state file, stopped when body is done: a restart is the next call.
const run = async <T>(file: string, body: (mail: ReturnType<typeof resendClient>, url: string, f: ResendFake) => Promise<T>): Promise<T> => {
  const f = resendFake({ stateFile: file });
  const up = await listen(f);
  try {
    return await body(resendClient("re_dev_fake", {}, { RESEND_BASE_URL: up.base }), up.base, f);
  } finally {
    up.server.close();
  }
};
describe("resend-fake with a state file", () => {

  it("keeps contacts, caught messages and idempotency keys across a restart", async () => {
    const file = stateFile();
    const before = await run(file, async (mail, _, f) => {
      for (const email of ["a@example.com", "b@example.com"]) await mail.contacts.create({ audienceId: "dev-audience", email });
      const email = await mail.emails.send({ from: "a@dev.invalid", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" }, { idempotencyKey: "k1" });
      const cast = await mail.broadcasts.create({ from: "n@dev.invalid", segmentId: "dev-audience", subject: "issue", html: "<p>issue</p>" });
      await mail.broadcasts.send(cast.data!.id);
      return { emailId: email.data!.id, castId: cast.data!.id, messages: structuredClone(f.messages()) };
    });
    await run(file, async (mail, _, f) => {
      const contacts = await mail.contacts.list({ segmentId: "dev-audience" });
      expect(contacts.data?.data.map((c) => c.email)).toEqual(["a@example.com", "b@example.com"]);
      expect(f.messages()).toEqual(before.messages);
      const again = await mail.emails.send({ from: "a@dev.invalid", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" }, { idempotencyKey: "k1" });
      expect(again.data?.id).toBe(before.emailId);
      expect((await mail.broadcasts.get(before.castId)).data?.status).toBe("sent");
      expect((await mail.broadcasts.send(before.castId)).error).toMatchObject({ statusCode: 422 });
      // An upsert of a kept address is the kept contact, not a second one.
      await mail.contacts.create({ audienceId: "dev-audience", email: "a@example.com" });
      expect(f.contacts("dev-audience")).toHaveLength(2);
    });
  });

  it("starts empty without a file and writes one on the first change", async () => {
    const file = stateFile();
    await run(file, async (mail, _, f) => {
      expect(f.messages()).toEqual([]);
      await mail.contacts.create({ audienceId: "dev-audience", email: "a@example.com" });
    });
    expect(JSON.parse(readFileSync(file, "utf8"))).toMatchObject({ segments: { "dev-audience": [{ email: "a@example.com" }] } });
  });

  it("POST /api/reset empties it, and the empty state is what the next start loads", async () => {
    const file = stateFile();
    await run(file, async (mail, url) => {
      await mail.contacts.create({ audienceId: "dev-audience", email: "a@example.com" });
      await mail.emails.send({ from: "a@dev.invalid", to: ["ops@dev.invalid"], subject: "held", html: "<p>x</p>" });
      expect((await fetch(`${url}/api/reset`, { method: "POST" })).status).toBe(400); // a cross-site form cannot
      const r = await fetch(`${url}/api/reset`, { method: "POST", headers: { "x-resend-fake-reset": "yes" } });
      expect(r.status).toBe(200);
      expect(await r.json()).toEqual({ cleared: { messages: 1, contacts: 1 } });
    });
    await run(file, async (mail, _, f) => {
      expect(f.messages()).toEqual([]);
      expect((await mail.contacts.list({ segmentId: "dev-audience" })).data?.data).toEqual([]);
    });
  });

  it("refuses to start over a state file it cannot read, rather than starting empty over it", () => {
    const file = stateFile();
    writeFileSync(file, "{not json");
    expect(() => resendFake({ stateFile: file })).toThrow(/state\.json/);
    expect(readFileSync(file, "utf8")).toBe("{not json");
  });
});
