import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { makeToken } from "./subscribe.js";
import { fakeData, testApp, testConfig } from "./testing.js";
import type { SiteData } from "./data.js";

const get = (app: ReturnType<typeof testApp>, path: string, headers: Record<string, string> = {}, method = "GET") => app.request(path, { method, headers });

// The pipeline's web template, whose markup the issue page's injections are anchored to.
const TEMPLATE = readFileSync("/app/newsroom/templates/digest-template.html", "utf8");
const ISSUE_HTML = TEMPLATE.replace("{{STYLES}}", "body{}").replaceAll(/\{\{[A-Z_]+\}\}/g, "");

const withIssue = (over: Partial<SiteData> = {}) =>
  fakeData({
    issue: async (d) => (d === "2026-09-01" ? { html: ISSUE_HTML, preheader: "A day's news" } : undefined),
    latestIssueDate: async () => "2026-09-01",
    ...over,
  });

// Tags and their bodies; a <style> element's CSS may name "<style>" in a comment (tokens.css does).
const inlineBlocks = (html: string) => [...html.matchAll(/<(script|style)\b([^>]*)>([\s\S]*?)<\/\1\s*>/gi)].map((m) => ({ kind: m[1]!.toLowerCase(), attrs: m[2]!, body: m[3]! }));
const sha = (body: string) => `'sha256-${createHash("sha256").update(body, "utf8").digest("base64")}'`;
const directive = (csp: string, name: string) => csp.split(";").map((d) => d.trim()).find((d) => d.startsWith(`${name} `)) ?? "";

describe("security headers", () => {
  const REQUIRED = {
    "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "strict-origin-when-cross-origin",
  };

  it.each(["/", "/sources", "/issues/2026-09-01", "/feed.xml", "/today", "/no/such/page", "/health"])("are on %s", async (path) => {
    const res = await get(testApp(withIssue()), path);
    for (const [k, v] of Object.entries(REQUIRED)) expect(res.headers.get(k), k).toBe(v);
    expect(res.headers.get("permissions-policy")).toMatch(/camera=\(\).*geolocation=\(\).*microphone=\(\)/);
    const csp = res.headers.get("content-security-policy") ?? "";
    expect(csp).not.toContain("nonce-");
    expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
    expect(csp).not.toMatch(/style-src [^;]*unsafe-inline/);
    expect(csp).toContain("frame-ancestors 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'none'");
  });

  it.each(["/", "/sources", "/stats", "/threads", "/search?q=x", "/feedback", "/issues/2026-09-01", "/no/such/page"])(
    "the CSP on %s allows every inline <script> and <style> by its hash, and is the same on every response",
    async (path) => {
      const app = testApp(withIssue());
      const [a, b] = [await get(app, path), await get(app, path)];
      const csp = a.headers.get("content-security-policy") ?? "";
      expect(b.headers.get("content-security-policy")).toBe(csp);
      const html = await a.text();
      expect(html).not.toMatch(/nonce=/);
      const blocks = inlineBlocks(html);
      expect(blocks.some((x) => x.kind === "script")).toBe(true);
      expect(blocks.some((x) => x.kind === "style")).toBe(true);
      for (const x of blocks) expect(directive(csp, `${x.kind}-src`), x.body.slice(0, 60)).toContain(sha(x.body));
    },
  );

  it("hashes the stored issue's own <style> blocks", async () => {
    const html = ISSUE_HTML.replace("</head>", "<style>.from-the-email{color:red}</style></head>");
    const res = await get(testApp(withIssue({ issue: async () => ({ html, preheader: "" }) })), "/issues/2026-09-01");
    expect(directive(res.headers.get("content-security-policy") ?? "", "style-src")).toContain(sha(".from-the-email{color:red}"));
  });

  it("hashes a CRLF stylesheet as the browser parses it, with LF", async () => {
    const html = ISSUE_HTML.replace("</head>", "<style>.a{}\r\n.b{}</style></head>");
    const res = await get(testApp(withIssue({ issue: async () => ({ html, preheader: "" }) })), "/issues/2026-09-01");
    expect(directive(res.headers.get("content-security-policy") ?? "", "style-src")).toContain(sha(".a{}\n.b{}"));
  });

  it("never blesses a script inside a stored issue: its hash is not in the header", async () => {
    const html = ISSUE_HTML.replace("</body>", "<script>alert(1)</script></body>");
    const res = await get(testApp(withIssue({ issue: async () => ({ html, preheader: "" }) })), "/issues/2026-09-01");
    expect(await res.text()).toContain("<script>alert(1)</script>");
    const scriptSrc = directive(res.headers.get("content-security-policy") ?? "", "script-src");
    expect(scriptSrc).toMatch(/^script-src( 'sha256-[A-Za-z0-9+/=]+')+$/);
    expect(scriptSrc).not.toContain(sha("alert(1)"));
  });

  it("allows no inline script on a response with none", async () => {
    const res = await get(testApp(withIssue()), "/feed.xml");
    expect(directive(res.headers.get("content-security-policy") ?? "", "script-src")).toBe("script-src 'none'");
  });
});

describe("the issue page", () => {
  it("injects the site's chrome at every needle of the real template", async () => {
    const error = vi.spyOn(console, "error");
    const html = await (await get(testApp(withIssue(), { cfg: testConfig({ CONTACT_EMAIL: "hi@digest.example" }) }), "/issues/2026-09-01")).text();
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
    expect(html).toContain('<a href="#main" class="skip-link">');
    expect(html).toContain('class="topbar"');
    expect(html).toContain('href="/issues/2026-09-01/translate"');
    expect(html).toContain("footer-feedback");
    expect(html).toContain('id="themeBtn"');
    expect(html).toContain('<meta property="og:title" content="News Digest – 2026-09-01">');
    expect(html).toContain('<link rel="alternate" type="text/markdown" href="/issues/2026-09-01.md">');
  });

  it("logs a missed injection as an error naming the needle and the date, and still serves the page", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const html = ISSUE_HTML.replace('<div class="paper">', '<div class="sheet">');
    const res = await get(testApp(withIssue({ issue: async () => ({ html, preheader: "" }) })), "/issues/2026-09-01");
    expect(res.status).toBe(200);
    const lines = error.mock.calls.map((c) => JSON.parse(String(c[0])) as Record<string, unknown>);
    error.mockRestore();
    expect(lines).toEqual([expect.objectContaining({ site: "issue", level: "error", date: "2026-09-01", needle: '<div class="paper">' })]);
  });

  it("negotiates Markdown, and an explicit .md never 406s", async () => {
    const app = testApp(withIssue());
    const md = await get(app, "/issues/2026-09-01", { accept: "text/markdown" });
    expect(md.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
    expect(md.headers.get("link")).toBe('</issues/2026-09-01>; rel="alternate"; type="text/html"');
    expect((await get(app, "/issues/2026-09-01", { accept: "image/png" })).status).toBe(406);
    expect((await get(app, "/issues/2026-09-01.md", { accept: "image/png" })).status).toBe(200);
  });

  it("is a friendly 404 for a date with no issue and for a non-date", async () => {
    const app = testApp(withIssue());
    for (const p of ["/issues/2019-01-01", "/issues/not-a-date"]) {
      const res = await get(app, p);
      expect(res.status).toBe(404);
      expect(await res.text()).toContain('<p class="nf-code">404</p>');
    }
  });

  it("answers a database failure with 503, never a 404 that hides the outage", async () => {
    const res = await get(testApp(withIssue({ issue: () => Promise.reject(new Error("db down")) })), "/issues/2026-09-01");
    expect(res.status).toBe(503);
  });
});

describe("routing as circulation answered", () => {
  it("serves a path with a trailing slash as the path", async () => {
    expect((await get(testApp(withIssue()), "/sources/")).status).toBe(200);
  });

  it.each([
    ["GET", "/subscribe", "POST"],
    ["POST", "/feed.xml", "GET,HEAD"],
    ["POST", "/2026-09-01", "GET,HEAD"],
  ])("answers %s %s with 405 and its Allow", async (method, path, allow) => {
    const res = await get(testApp(withIssue()), path, {}, method);
    expect(res.status).toBe(405);
    expect(res.headers.get("allow")).toBe(allow);
  });

  it("answers HEAD on a GET route", async () => {
    const res = await get(testApp(withIssue()), "/feed.xml", {}, "HEAD");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("");
  });

  it.each([
    ["/today", 307, "/issues/2026-09-01"],
    ["/today/translate?lang=de", 307, "/issues/2026-09-01/translate?lang=de"],
    ["/today/translate?lang=en", 307, "/issues/2026-09-01/translate"],
    ["/2026-09-01", 308, "/issues/2026-09-01"],
    ["/2026-09-01.md", 308, "/issues/2026-09-01.md"],
    ["/2026-09-01/translate?lang=fr", 308, "/issues/2026-09-01/translate?lang=fr"],
    ["/translate?to=//evil.example", 307, "https://digest-example.translate.goog/?_x_tr_sl=en&_x_tr_tl=fr&_x_tr_hl=fr"],
    ["/llms-full.txt", 307, "/index.md"],
  ])("redirects %s", async (path, status, location) => {
    const res = await get(testApp(withIssue()), path);
    expect(res.status).toBe(status);
    expect(res.headers.get("location")).toBe(location);
  });

  it("refuses half a threads cursor", async () => {
    expect((await get(testApp(withIssue()), "/threads?before=2026-09-01")).status).toBe(400);
    expect((await get(testApp(withIssue()), "/threads/more?before=&before_id=1")).status).toBe(400);
  });

  it("answers a malformed number the way the typed query did: 400", async () => {
    expect((await get(testApp(withIssue()), "/stats.json?days=abc")).status).toBe(400);
  });
});

const mail = () => ({ contacts: { create: vi.fn(async () => ({ data: { id: "c", object: "contact" as const }, error: null, headers: null })) }, emails: { send: vi.fn(async () => ({ data: { id: "e" }, error: null, headers: null })) } });
const post = (app: ReturnType<typeof testApp>, email: string, ip = "198.51.100.9") =>
  app.request("/subscribe", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", "x-forwarded-for": ip }, body: `email=${encodeURIComponent(email)}` });

describe("subscribe", () => {
  const SUBS = { RESEND_API_KEY: "re_test", RESEND_AUDIENCE_ID: "aud", RESEND_FROM: "digest@send.digest.example", SUBSCRIBE_TOKEN_SECRET: "a-secret-of-at-least-16" };

  it("mails a signed confirmation link, and never logs the address", async () => {
    const m = mail();
    const logs = [vi.spyOn(console, "log"), vi.spyOn(console, "warn"), vi.spyOn(console, "error")];
    const app = testApp(fakeData(), { cfg: testConfig(SUBS), mail: m });
    const res = await post(app, "Reader@Gmail.com");
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/?pending=1");
    expect(m.contacts.create).not.toHaveBeenCalled();
    const sent = (m.emails.send.mock.calls[0] as unknown as [{ to: string[]; html: string }])[0];
    expect(sent.to).toEqual(["reader@gmail.com"]);
    expect(sent.html).toMatch(/https:\/\/digest\.example\/confirm\?token=[\w-]+\.[\w-]+/);
    for (const l of logs) {
      for (const call of l.mock.calls) expect(JSON.stringify(call)).not.toMatch(/reader@gmail\.com/i);
      l.mockRestore();
    }
  });

  it("adds the contact only once the link is followed", async () => {
    const m = mail();
    const app = testApp(fakeData(), { cfg: testConfig(SUBS), mail: m });
    const token = makeToken(SUBS.SUBSCRIBE_TOKEN_SECRET, "reader@gmail.com", Math.floor(Date.parse("2026-09-24T00:00:00Z") / 1000));
    const res = await get(app, `/confirm?token=${token}`);
    expect(res.headers.get("location")).toBe("/?subscribed=1");
    expect(m.contacts.create).toHaveBeenCalledWith({ audienceId: "aud", email: "reader@gmail.com" });
    expect((await get(app, "/confirm?token=forged.token")).headers.get("location")).toBe("/?subscribe_error=1");
  });

  it("refuses an invalid or disposable address before spending a request", async () => {
    const m = mail();
    const app = testApp(fakeData(), { cfg: testConfig(SUBS), mail: m });
    for (const e of ["not-an-address", "someone@yopmail.com"]) expect((await post(app, e)).headers.get("location")).toBe("/?subscribe_invalid=1");
    expect(m.emails.send).not.toHaveBeenCalled();
  });

  it("allows five attempts an hour from one address", async () => {
    const app = testApp(fakeData(), { cfg: testConfig(SUBS), mail: mail() });
    const locations = [];
    for (let i = 0; i < 6; i++) locations.push((await post(app, "reader@gmail.com", "203.0.113.50")).headers.get("location"));
    expect(locations.slice(0, 5).every((l) => l === "/?pending=1")).toBe(true);
    expect(locations[5]).toBe("/?subscribe_ratelimited=1");
    expect((await post(app, "reader@gmail.com", "203.0.113.51")).headers.get("location")).toBe("/?pending=1");
  });

  it("adds directly only when double opt-in is switched off on purpose", async () => {
    const m = mail();
    const app = testApp(fakeData(), { cfg: testConfig({ ...SUBS, SUBSCRIBE_DOUBLE_OPT_IN: "false" }), mail: m });
    expect((await post(app, "reader@gmail.com")).headers.get("location")).toBe("/?subscribed=1");
    expect(m.contacts.create).toHaveBeenCalledOnce();
    expect(m.emails.send).not.toHaveBeenCalled();
  });

  it("shows the subscribe band only when subscriptions are configured", async () => {
    const on = await (await get(testApp(fakeData({ indexMeta: async () => ({ total: 0, firstDate: null, newestDate: null, totalStories: 0 }) }), { cfg: testConfig(SUBS) }), "/")).text();
    expect(on).toContain('id="subscribe"');
    expect(await (await get(testApp(fakeData()), "/")).text()).not.toContain('id="subscribe"');
  });
});

describe("the retired /ask and MCP surface", () => {
  it.each(["/ask", "/ask.json", "/connect", "/mcp", "/mcp/tools.json", "/mcp/tools/get_issue.json", "/.well-known/mcp.json", "/.well-known/mcp/server-card.json"])("%s is the ordinary 404 page", async (path) => {
    const app = testApp(withIssue());
    const res = await get(app, path);
    expect(res.status).toBe(404);
    expect(await res.text()).toContain('<p class="nf-code">404</p>');
    // A POST is refused as any other unknown path is (a 405 where the legacy /:date route matches).
    expect([404, 405]).toContain((await get(app, path, {}, "POST")).status);
  });

  it("is linked from nowhere", async () => {
    const app = testApp(withIssue());
    for (const path of ["/", "/sources", "/issues/2026-09-01", "/llms.txt", "/index.md"]) {
      const body = await (await get(app, path)).text();
      expect(body, path).not.toMatch(/\/(ask|connect|mcp)\b/);
    }
  });
});

describe("health", () => {
  it("is healthy when the database answers, 503 when it does not", async () => {
    expect(await (await get(testApp(fakeData()), "/health")).json()).toEqual({ status: "healthy" });
    const down = await get(testApp(fakeData({ ping: () => Promise.reject(new Error("down")) })), "/health");
    expect(down.status).toBe(503);
  });
});

// Found by review (2026-09-23), each reproduced live before the fix.
const stream = (chunks: Uint8Array[], fail = false) =>
  new ReadableStream<Uint8Array>({
    start(ctl) {
      for (const c of chunks) ctl.enqueue(c);
      if (fail) ctl.error(new Error("aborted"));
      else ctl.close();
    },
  });

describe("hostile requests", () => {
  it("refuses a body over the limit, chunked or not, before reading it whole", async () => {
    const big = stream(Array.from({ length: 40 }, () => new Uint8Array(16 * 1024).fill(97)));
    const res = await testApp(fakeData()).request("/subscribe", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: big, duplex: "half" } as RequestInit);
    expect(res.status).toBe(413);
  });

  it.each(["/threads?before=garbage&before_id=1", "/threads/more?before=2026-09-01%2000:00:00.5&before_id=1"])("refuses a cursor the site never wrote: %s", async (path) => {
    expect((await testApp(fakeData()).request(path)).status).toBe(400);
  });
});
