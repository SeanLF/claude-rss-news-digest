import { type Context, Hono } from "hono";
import { streamSSE } from "hono/streaming";
import { ANSWER_TIMEOUT_MS, AskError, AskState, MAX_BODY_BYTES, MAX_QUESTION, admit, answer, replay } from "./ask.js";
import { type ArchivePage, biasMap, fetchArchive, fragmentHtml, DEFAULT_LIMIT } from "./archive.js";
import { type Assets, FAVICON_SVG } from "./assets.js";
import { type SiteConfig, baseUrl, subscriptionsEnabled } from "./config.js";
import type { SiteData } from "./data.js";
import { FEED_ENTRY_LIMIT, atomFeed } from "./feed.js";
import { htmlLinkHeader, indexMarkdown, issueMarkdown, markdownLinkHeader, negotiate, hiddenPointer } from "./markdown.js";
import { CACHE_MAX_AGE_S, MAX_ARGUMENT_LENGTH, INSTRUCTIONS, TOOLS, type ToolDeps, allow, callTool, coerceArguments, handleRpc, listing, llmsSection, mcpLimits, sanitizeQuery, serverCard, toolsJson, SEARCH_LIMIT } from "./mcp.js";
import { NOTICES, type IndexScope, indexPage } from "./pages/index.js";
import { issuePage } from "./pages/issue.js";
import type { PageCtx } from "./pages/chrome.js";
import { askPage, connectPage, feedbackPage, notFoundPage, searchPage, sourcesPage, statsPage } from "./pages/sub.js";
import { threadPage, threadsFragment, threadsPage } from "./pages/threads.js";
import { RateLimiter, clientKey } from "./ratelimit.js";
import { securityHeaders } from "./security.js";
import { type CatalogueEntry, sourceRows } from "./sources.js";
import { computeMetrics, statsFrom, statsJson, statsValue } from "./stats.js";
import { type Mail, CONFIRM_TTL_S, addContact, isValidEmail, makeToken, sendConfirmation, verifyToken } from "./subscribe.js";
import { OLDER_PAGE, threadDetail, threadIndex } from "./threads.js";
import { isValidDate } from "./text.js";
import { proxyTarget, validQueryLang, validTranslatePath } from "./translate.js";

// The web tier (docs/2026-09-23-web-tier-typescript-fork.md): every route circulation served, from the
// product database, behind the security headers.

export interface SiteDeps {
  cfg: SiteConfig;
  assets: Assets;
  catalogue: CatalogueEntry[];
  data: SiteData;
  // The Resend client, when subscriptions are configured.
  mail: Mail | undefined;
  ask: AskState;
  now: () => Date;
}

const md = (c: Context, body: string, link: string, status = 200) =>
  c.body(body, status as 200, { "content-type": "text/markdown; charset=utf-8", vary: "accept", link });
const text = (c: Context, body: string, status: number) => c.body(body, status as 200, { "content-type": "text/plain; charset=utf-8" });
const notAcceptable = (c: Context) => c.body("Not Acceptable — this URL is available as text/html or text/markdown.\n", 406, { "content-type": "text/plain; charset=utf-8", vary: "accept" });
const cachedJson = (c: Context, body: string) => c.body(body, 200, { "content-type": "application/json", "cache-control": `public, max-age=${CACHE_MAX_AGE_S}` });
const noStore = (c: Context, status: number, body: string) => c.body(body, status as 200, { "content-type": "application/json", "cache-control": "no-store" });
// A redirect the Rust server sent: 303 after a form, 307 temporary, 308 permanent.
const redirect = (c: Context, to: string, status: 303 | 307 | 308) => c.body(null, status, { location: to });

// A query parameter parsed as the Rust server's typed Query did: absent, a number, or a 400.
function intQuery(c: Context, name: string, unsigned: boolean): number | undefined | Response {
  const raw = c.req.query(name);
  if (raw === undefined) return undefined;
  const ok = unsigned ? /^\+?\d+$/.test(raw) && Number(raw) <= 0xffffffff : /^[+-]?\d+$/.test(raw) && Number.isSafeInteger(Number(raw));
  return ok ? Number(raw) : text(c, `Failed to deserialize query string: ${name}: invalid digit found in string`, 400);
}

// The paths the site answers and their methods, for a 405 (with Allow) where a path exists and the
// method does not, as axum answered.
const METHODS: [RegExp, string][] = [
  [/^\/(subscribe|ask\.json)$/, "POST"],
  [/^\/(mcp|ask)$/, "GET,HEAD,POST"],
  [/^\/(|confirm|privacy|health|favicon\.ico|robots\.txt|llms\.txt|llms-full\.txt|index\.md|apple-touch-icon(-precomposed)?\.png|og-image\.png|sources|feed\.xml|stats|stats\.json|archive|threads|threads\/more|search|feedback|connect|today|translate|today\/translate|\.well-known\/mcp\.json|\.well-known\/mcp\/server-card\.json|mcp\/tools\.json)$/, "GET,HEAD"],
  [/^\/(thread|issues|mcp\/tools)\/[^/]+$/, "GET,HEAD"],
  [/^\/issues\/[^/]+\/translate$/, "GET,HEAD"],
  [/^\/assets\/fonts\/[^/]+$/, "GET,HEAD"],
  [/^\/[^/]+(\/translate)?$/, "GET,HEAD"],
];

// The largest request body any route reads: /ask's history cap, with room for the JSON around it.
const MAX_REQUEST_BYTES = 128 * 1024;

// A database failure is a 503 with a generic sentence, logged; never a page that hides the outage.
function unavailable(c: Context, what: string, e: unknown): Response {
  console.error(JSON.stringify({ site: "db", path: c.req.path, error: String(e) }));
  return text(c, what, 503);
}

const bytes = (c: Context, b: Buffer | string, type: string, cache: string) => c.body(typeof b === "string" ? b : new Uint8Array(b), 200, { "content-type": type, "cache-control": cache });

// The threads cursor is usable only as a pair; half of one, or an empty `before`, is refused rather
// than quietly served as the newest page.
function cursor(c: Context): { updatedAt: string; id: number } | undefined | Response {
  const before = c.req.query("before");
  const id = intQuery(c, "before_id", false);
  if (id instanceof Response) return id;
  if (before === undefined && id === undefined) return undefined;
  // Only the cursor the site itself writes: whole seconds, "YYYY-MM-DD HH:MM:SS". Anything else would
  // fail a timestamp cast, or with a fraction repeat that second's rows.
  if (before && id !== undefined && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/.test(before)) return { updatedAt: before, id };
  if (before && id !== undefined) return text(c, "before must be a timestamp of the form YYYY-MM-DD HH:MM:SS", 400);
  return text(c, "before and before_id must be supplied together, and before must not be empty", 400);
}

async function readQuestion(c: Context): Promise<{ question: string; history: ReturnType<typeof replay> } | AskError> {
  let body: string;
  try {
    body = await c.req.text();
  } catch {
    return new AskError(400, "Could not read that question.");
  }
  if (Buffer.byteLength(body) > MAX_BODY_BYTES) return new AskError(413, "That history is too long.");
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return new AskError(400, "Could not read that question.");
  }
  if (!parsed || typeof parsed !== "object" || !("question" in parsed) || typeof parsed.question !== "string") return new AskError(400, "Could not read that question.");
  const question = Array.from(parsed.question.trim()).slice(0, MAX_QUESTION).join("");
  if (!question) return new AskError(400, "Ask a question first.");
  return { question, history: replay("history" in parsed ? parsed.history : undefined) };
}

export function siteApp(deps: SiteDeps): Hono {
  const { cfg, assets, catalogue, data } = deps;
  const base = baseUrl(cfg);
  const bias = biasMap(catalogue);
  const tools: ToolDeps = { data, catalogue, bias, digestName: cfg.digestName, base, now: deps.now };
  const limits = mcpLimits();
  // Five attempts an hour from one address: far above a person, low enough to stop signup bombing.
  const subscribeLimiter = new RateLimiter(5, 3_600_000);
  const names = new Map(catalogue.map((s) => [s.id, s.name]));
  const app = new Hono({ strict: false });
  const ctx: PageCtx = { cfg, assets };
  const page404 = (c: Context, heading = "Page not found", message = "There's nothing at this address.") => c.html(notFoundPage(ctx, heading, message), 404);
  const nowMs = () => deps.now().getTime();

  app.use(...securityHeaders());
  // Every body is bounded before anything reads it, chunked or not: axum's extractors capped at 2 MB,
  // and an unbounded read is a way to exhaust the container's memory with one request.
  // Read here, once, so a client that hangs up mid-body is a 400 before any handler runs (or any /ask
  // slot is taken), not a 500 out of a handler.
  app.use(async (c, next) => {
    if (!c.req.raw.body) return next();
    const reader = c.req.raw.body.getReader();
    const parts: Uint8Array[] = [];
    let size = 0;
    try {
      for (let r = await reader.read(); !r.done; r = await reader.read()) {
        size += r.value.byteLength;
        if (size > MAX_REQUEST_BYTES) {
          await reader.cancel().catch(() => undefined);
          return text(c, "Payload Too Large", 413);
        }
        parts.push(r.value);
      }
    } catch {
      return text(c, "Bad Request: the body did not arrive", 400);
    }
    c.req.raw = new Request(c.req.raw, { method: c.req.raw.method, body: Buffer.concat(parts) });
    return next();
  });
  app.onError((e, c) => {
    console.error(JSON.stringify({ site: "error", path: c.req.path, error: String(e) }));
    return text(c, "Internal Server Error", 500);
  });

  // ── the index and the archive ──
  const indexMd = async (c: Context) => {
    const [meta, page] = await Promise.all([data.indexMeta(), fetchArchive(data, bias, { limit: 100 })]);
    return md(c, indexMarkdown(cfg.digestName, meta, page.issues, base), htmlLinkHeader("/"));
  };
  app.get("/", async (c) => {
    const year = intQuery(c, "year", false);
    if (year instanceof Response) return year;
    const before = c.req.query("before");
    try {
      if (before === undefined && year === undefined) {
        const want = negotiate(c.req.header("accept"));
        if (want === "not-acceptable") return notAcceptable(c);
        if (want === "markdown") return await indexMd(c);
      }
      const meta = await data.indexMeta();
      const scope: IndexScope = year !== undefined ? { kind: "year", year } : before !== undefined ? { kind: "before", before } : { kind: "all" };
      let page: ArchivePage | undefined;
      if (meta.total > 0) page = await fetchArchive(data, bias, scope.kind === "year" ? { year: scope.year, limit: 100 } : { before, limit: DEFAULT_LIMIT });
      const notice = ["subscribed", "pending", "subscribe_invalid", "subscribe_ratelimited", "subscribe_error"].find((k) => c.req.query(k) !== undefined);
      const html = indexPage(ctx, meta, scope, page, notice ? NOTICES[notice]! : "", hiddenPointer(`${base}/index.md`));
      return c.html(html, 200, { vary: "accept", link: markdownLinkHeader("/index.md") });
    } catch (e) {
      return unavailable(c, "Service unavailable", e);
    }
  });
  app.get("/index.md", async (c) => {
    try {
      return await indexMd(c);
    } catch (e) {
      return unavailable(c, "Service unavailable", e);
    }
  });
  app.get("/archive", async (c) => {
    const year = intQuery(c, "year", false);
    if (year instanceof Response) return year;
    const limit = intQuery(c, "limit", false);
    if (limit instanceof Response) return limit;
    try {
      return c.html(fragmentHtml(await fetchArchive(data, bias, { before: c.req.query("before"), year, limit: limit ?? DEFAULT_LIMIT })));
    } catch (e) {
      return unavailable(c, "Service unavailable", e);
    }
  });

  // ── issues ──
  app.get("/issues/:date/translate", async (c) => {
    const date = c.req.param("date");
    if (!isValidDate(date)) return text(c, "Invalid date format", 400);
    try {
      if (!(await data.issue(date))) return text(c, "Digest not found", 404);
    } catch (e) {
      return unavailable(c, "Digest unavailable", e);
    }
    return redirect(c, proxyTarget(cfg.digestDomain, `/issues/${date}`, c.req.query("lang"), c.req.header("accept-language")), 307);
  });
  app.get("/issues/:date", async (c) => {
    const raw = c.req.param("date");
    // An explicit .md URL forces Markdown and never 406s.
    const explicit = raw.endsWith(".md");
    const date = explicit ? raw.slice(0, -3) : raw;
    if (!isValidDate(date)) return page404(c);
    const want = explicit ? "markdown" : negotiate(c.req.header("accept"));
    if (want === "not-acceptable") return notAcceptable(c);
    let stored;
    try {
      stored = await data.issue(date);
    } catch (e) {
      return unavailable(c, "Digest unavailable", e);
    }
    if (!stored) return page404(c, "No issue for that date", `There's no issue dated ${date} in the archive — it may not have been published, or the date is off by a day.`);
    if (want === "markdown") {
      const body = issueMarkdown(stored.html, cfg.digestName, date);
      return body ? md(c, body, htmlLinkHeader(`/issues/${date}`)) : text(c, "Digest unavailable", 503);
    }
    return c.html(issuePage(ctx, date, stored, `${base}/issues/${date}.md`), 200, { vary: "accept", link: markdownLinkHeader(`/issues/${date}.md`) });
  });
  const latest = async (c: Context, then: (date: string) => Response) => {
    let date;
    try {
      date = await data.latestIssueDate();
    } catch (e) {
      return unavailable(c, "Digest unavailable", e);
    }
    return date ? then(date) : text(c, "No digests yet", 404);
  };
  app.get("/today", (c) => latest(c, (d) => redirect(c, `/issues/${d}`, 307)));
  app.get("/today/translate", (c) => {
    const lang = validQueryLang(c.req.query("lang"));
    return latest(c, (d) => redirect(c, `/issues/${d}/translate${lang ? `?lang=${lang}` : ""}`, 307));
  });
  app.get("/translate", (c) => redirect(c, proxyTarget(cfg.digestDomain, validTranslatePath(c.req.query("to")) ?? "/", c.req.query("lang"), c.req.header("accept-language")), 307));

  // ── the feed and the discovery files ──
  app.get("/feed.xml", async (c) => {
    try {
      return c.body(atomFeed(cfg.digestName, base, await data.feed(FEED_ENTRY_LIMIT)), 200, { "content-type": "application/atom+xml; charset=utf-8" });
    } catch (e) {
      return unavailable(c, "Service unavailable", e);
    }
  });
  app.get("/llms.txt", (c) => {
    const link = (p: string) => `${base.replace(/\/+$/, "")}${p}`;
    let body = `# ${cfg.digestName}\n\n> An automated daily news briefing on geopolitics, tech, and privacy. It reads feeds across five continents, clusters the day's stories, writes a bias-labelled digest, fact-checks itself against the sources, and sends. Curated and written by Claude; no human edits any issue.\n\n## Read the briefing\n\n`;
    body += `- [Latest issue](${link("/today")}): redirects to the newest dated issue; append \`.md\` or send \`Accept: text/markdown\` for Markdown.\n`;
    body += `- [Archive index](${link("/index.md")}): every issue as Markdown, newest first, each linking to its \`.md\`.\n`;
    body += "\n## Reference\n\n";
    body += `- [Sources and bias ratings](${link("/sources")}): every source with its Media Bias/Fact Check bias and factuality rating.\n`;
    body += `- [Transparency stats](${link("/stats")}) ([JSON](${link("/stats.json")})): subscriber count, source-spectrum balance, and AI cost per issue.\n`;
    if (cfg.sourceUrl) body += `- [Source code](${cfg.sourceUrl})\n`;
    body += "\nEvery dated issue at `/issues/YYYY-MM-DD` also serves Markdown at `/issues/YYYY-MM-DD.md`.\n";
    body += llmsSection(base);
    return c.body(body, 200, { "content-type": "text/markdown; charset=utf-8" });
  });
  // Redirects to the index rather than concatenating every issue: that grows without bound and is mostly stale.
  app.get("/llms-full.txt", (c) => redirect(c, "/index.md", 307));
  // Crawlable, and consenting to be read and cited but not trained on (Content-Signal).
  app.get("/robots.txt", (c) => text(c, "User-agent: *\nContent-Signal: search=yes, ai-input=yes, ai-train=no\nAllow: /\n", 200));
  app.get("/privacy", (c) => redirect(c, cfg.homepageUrl ? `${cfg.homepageUrl.replace(/\/+$/, "")}/privacy` : "https://seanfloyd.dev/privacy", 307));
  app.get("/health", async (c) => {
    try {
      await data.ping();
      return c.json({ status: "healthy" });
    } catch (e) {
      console.error(JSON.stringify({ site: "health", error: String(e) }));
      return c.json({ status: "degraded" }, 503);
    }
  });

  // ── static assets ──
  app.get("/favicon.ico", (c) => bytes(c, FAVICON_SVG, "image/svg+xml", "public, max-age=86400"));
  app.get("/apple-touch-icon.png", (c) => bytes(c, assets.appleTouchIcon, "image/png", "public, max-age=86400"));
  app.get("/apple-touch-icon-precomposed.png", (c) => bytes(c, assets.appleTouchIcon, "image/png", "public, max-age=86400"));
  app.get("/og-image.png", (c) => bytes(c, assets.ogImage, "image/png", "public, max-age=31536000, immutable"));
  // The path carries the font's hash, so it is cached for a year.
  app.get(assets.fontUrl, (c) => bytes(c, assets.font, "font/woff2", "public, max-age=31536000, immutable"));

  // ── pages ──
  app.get("/sources", (c) => c.html(sourcesPage(ctx, sourceRows(catalogue))));
  app.get("/feedback", (c) => c.html(feedbackPage(ctx)));
  app.get("/search", async (c) => {
    const q = sanitizeQuery(c.req.query("q") ?? "");
    try {
      return c.html(searchPage(ctx, q, q === undefined ? [] : await data.search(q, SEARCH_LIMIT)));
    } catch (e) {
      return unavailable(c, "Search unavailable", e);
    }
  });
  const statsFor = async (days: number) => statsFrom(await data.stats(days, deps.now()), days, catalogue);
  app.get("/stats", async (c) => {
    const d = intQuery(c, "days", true);
    if (d instanceof Response) return d;
    const days = d === 7 || d === 90 ? d : 30;
    try {
      const s = await statsFor(days);
      return c.html(statsPage(ctx, days, s, computeMetrics(s, catalogue), names));
    } catch (e) {
      return unavailable(c, "Stats unavailable", e);
    }
  });
  app.get("/stats.json", async (c) => {
    const d = intQuery(c, "days", true);
    if (d instanceof Response) return d;
    try {
      return c.body(statsJson(statsValue(await statsFor(d ?? 30))), 200, { "content-type": "application/json" });
    } catch (e) {
      return unavailable(c, "Stats unavailable", e);
    }
  });

  // ── threads ──
  const threadsRoute = (fragment: boolean) => async (c: Context) => {
    const cur = cursor(c);
    if (cur instanceof Response) return cur;
    const limit = intQuery(c, "limit", false);
    if (limit instanceof Response) return limit;
    try {
      const page = await threadIndex(data, cur, limit ?? OLDER_PAGE);
      return c.html(fragment ? threadsFragment(page) : threadsPage(ctx, page, cur !== undefined));
    } catch (e) {
      return unavailable(c, "Threads unavailable", e);
    }
  };
  app.get("/threads", threadsRoute(false));
  app.get("/threads/more", threadsRoute(true));
  app.get("/thread/:id", async (c) => {
    const raw = c.req.param("id");
    if (!/^[+-]?\d+$/.test(raw) || !Number.isSafeInteger(Number(raw))) return text(c, "Thread not found", 404);
    const id = Number(raw);
    let d;
    try {
      d = await threadDetail(data, id);
    } catch (e) {
      return unavailable(c, "Thread unavailable", e);
    }
    if (!d) return text(c, "Thread not found", 404);
    if (d.mergedInto !== null) return redirect(c, `/thread/${d.mergedInto}`, 308);
    return c.html(threadPage(ctx, id, d));
  });

  // ── subscribe ──
  // POST-only paths a GET would otherwise reach through the legacy /:date route.
  for (const path of ["/subscribe", "/ask.json"]) app.get(path, (c) => c.body(null, 405, { allow: "POST" }));
  app.post("/subscribe", async (c) => {
    const form: Record<string, unknown> = await c.req.parseBody().catch(() => ({}));
    const email = typeof form["email"] === "string" ? form["email"].trim().toLowerCase() : "";
    // The address is never logged, and the IP only ever keys the limiter.
    if (!isValidEmail(email)) return redirect(c, "/?subscribe_invalid=1", 303);
    if (!subscribeLimiter.check(clientKey(c.req.header("x-forwarded-for")), nowMs())) return redirect(c, "/?subscribe_ratelimited=1", 303);
    if (!subscriptionsEnabled(cfg) || !deps.mail) {
      console.error(JSON.stringify({ site: "subscribe", error: "subscriptions are not configured" }));
      return redirect(c, "/?subscribe_error=1", 303);
    }
    if (cfg.doubleOptIn) {
      // siteConfig refused to start without the secret, the domain and a sender.
      const token = makeToken(cfg.subscribeTokenSecret!, email, Math.floor(nowMs() / 1000) + CONFIRM_TTL_S);
      const ok = await sendConfirmation(cfg, deps.mail, email, `${base}/confirm?token=${token}`);
      if (ok) console.log(JSON.stringify({ site: "subscribe", event: "confirmation sent" }));
      return redirect(c, ok ? "/?pending=1" : "/?subscribe_error=1", 303);
    }
    const ok = await addContact(cfg, deps.mail, email);
    if (ok) console.log(JSON.stringify({ site: "subscribe", event: "contact added directly (double opt-in off)" }));
    return redirect(c, ok ? "/?subscribed=1" : "/?subscribe_error=1", 303);
  });
  app.get("/confirm", async (c) => {
    if (!cfg.subscribeTokenSecret || !deps.mail || !subscriptionsEnabled(cfg)) return redirect(c, "/?subscribe_error=1", 303);
    const email = verifyToken(cfg.subscribeTokenSecret, c.req.query("token") ?? "", Math.floor(nowMs() / 1000));
    if (!email) {
      console.log(JSON.stringify({ site: "confirm", event: "invalid or expired token" }));
      return redirect(c, "/?subscribe_error=1", 303);
    }
    const ok = await addContact(cfg, deps.mail, email);
    if (ok) console.log(JSON.stringify({ site: "confirm", event: "contact added" }));
    return redirect(c, ok ? "/?subscribed=1" : "/?subscribe_error=1", 303);
  });

  // ── MCP ──
  app.get("/mcp", (c) => c.body(listing(cfg.digestName, base), 200, { "content-type": "text/markdown; charset=utf-8", "cache-control": `public, max-age=${CACHE_MAX_AGE_S}` }));
  app.post("/mcp", async (c) => {
    if (!allow(limits, c.req.header("x-forwarded-for"), nowMs())) return noStore(c, 429, '{"error":"rate limited"}');
    // A JSON-RPC batch is refused: the limit counts requests, and a batch of a hundred calls in one
    // request would pass it a hundred times over. rmcp served no batches either.
    const body = await c.req.text();
    if (/^\s*\[/.test(body)) return noStore(c, 400, JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "batches are not supported" } }));
    return handleRpc(tools, new Request(c.req.raw, { method: "POST", body }));
  });
  const card = (c: Context) => cachedJson(c, JSON.stringify(serverCard(cfg.digestName, base)));
  app.get("/.well-known/mcp.json", card);
  app.get("/.well-known/mcp/server-card.json", card);
  app.get("/mcp/tools.json", (c) => cachedJson(c, JSON.stringify(toolsJson(base))));
  app.get("/mcp/tools/:name", async (c) => {
    // Metered before the lookup: an enumerable URL must not be a free way to churn the cache with 404s.
    if (!allow(limits, c.req.header("x-forwarded-for"), nowMs())) return noStore(c, 429, '{"error":"rate limited"}');
    const raw = c.req.param("name");
    const tool = raw.endsWith(".json") ? TOOLS.find((t) => t.name === raw.slice(0, -5)) : undefined;
    if (!tool) return noStore(c, 404, '{"error":"no such tool"}');
    const params = c.req.query();
    if (Object.values(params).some((v) => v.length > MAX_ARGUMENT_LENGTH)) return noStore(c, 400, JSON.stringify({ error: "argument too long", max_length: MAX_ARGUMENT_LENGTH }));
    const coerced = coerceArguments(tool, params);
    if ("missing" in coerced) return noStore(c, 400, JSON.stringify({ error: "missing required argument(s)", missing: coerced.missing, schema: tool.inputSchema }));
    const r = await callTool(tools, tool.name, coerced.args);
    // The grounding stance rides on every bridge answer: this door's clients never see initialize.
    return cachedJson(c, JSON.stringify({ tool: tool.name, instructions: INSTRUCTIONS, content: [{ type: "text", text: r.text }], ...(r.isError ? { is_error: true } : {}) }));
  });
  app.get("/connect", (c) => {
    // Every command on the page is pasted into a terminal, so it must be absolute: the configured
    // domain, else the origin this request came in on (a clone running locally).
    const origin = base || requestOrigin(c.req.header("host"), c.req.header("x-forwarded-proto"));
    return c.html(connectPage(ctx, origin, TOOLS));
  });

  // ── ask ──
  app.get("/ask", (c) => c.html(askPage(ctx, base, deps.ask.config ? { model: deps.ask.config.models[0]!, provider: deps.ask.config.providerLabel, openrouter: deps.ask.config.openrouter } : undefined)));
  app.post("/ask", async (c) => {
    if (Number(c.req.header("content-length") ?? 0) > MAX_BODY_BYTES) return text(c, "That history is too long.", 413);
    // The question is read before a slot is taken: a client that hangs up mid-body must not hold one.
    const q = await readQuestion(c);
    if (q instanceof AskError) return text(c, q.message, q.status);
    const admitted = admit(deps.ask, c.req.header("x-forwarded-for"), nowMs());
    if (admitted instanceof AskError) return text(c, admitted.message, admitted.status);
    return streamSSE(c, async (stream) => {
      const abort = new AbortController();
      const deadline = setTimeout(() => abort.abort(), ANSWER_TIMEOUT_MS);
      c.req.raw.signal.addEventListener("abort", () => abort.abort(), { once: true });
      // A comment every 15 s so an idle intermediary does not cut a stream that is mid-tool-call.
      const keepAlive = setInterval(() => void stream.write(": keep-alive\n\n"), 15_000);
      stream.onAbort(() => abort.abort());
      const send = (event: string, line: string) => void stream.writeSSE({ event, data: line });
      try {
        await answer(admitted.cfg, tools, q.question, q.history, (p) => {
          if (abort.signal.aborted) return false;
          if (p.kind === "tool") send("tool", p.label);
          else if (p.kind === "model") send("model", p.name);
          else send("answer", p.text);
          return true;
        }, abort.signal);
      } catch (e) {
        if (!stream.aborted) send("failed", e instanceof AskError ? e.message : abort.signal.aborted ? "That took too long to answer. Try a narrower question." : "That did not work.");
        if (!(e instanceof AskError)) console.error(JSON.stringify({ site: "ask", error: String(e) }));
      } finally {
        clearTimeout(deadline);
        clearInterval(keepAlive);
        admitted.release();
        if (!stream.aborted) await stream.writeSSE({ event: "done", data: "1" });
      }
    });
  });
  app.post("/ask.json", async (c) => {
    if (Number(c.req.header("content-length") ?? 0) > MAX_BODY_BYTES) return c.json({ error: "history too long" }, 413);
    const q = await readQuestion(c);
    if (q instanceof AskError) return c.json({ error: q.status === 413 ? "history too long" : q.status === 400 && q.message.startsWith("Ask") ? "empty question" : "could not read that question" }, q.status as 400);
    const admitted = admit(deps.ask, c.req.header("x-forwarded-for"), nowMs());
    if (admitted instanceof AskError) return c.json({ error: admitted.message }, admitted.status as 429);
    const abort = new AbortController();
    const deadline = setTimeout(() => abort.abort(), ANSWER_TIMEOUT_MS);
    // A client that hangs up stops the answer: nobody is left to read it, and it spends a paid provider.
    c.req.raw.signal.addEventListener("abort", () => abort.abort(), { once: true });
    try {
      const steps: string[] = [];
      let model: string | null = null;
      let result = "";
      await answer(admitted.cfg, tools, q.question, q.history, (p) => {
        if (p.kind === "tool") steps.push(p.label);
        else if (p.kind === "model") model = p.name;
        else result = p.text;
        return true;
      }, abort.signal);
      return c.json({ answer: result, model, steps });
    } catch (e) {
      if (e instanceof AskError) return c.json({ error: e.message }, e.status as 502);
      if (abort.signal.aborted) return c.json({ error: "timed out" }, 504);
      throw e;
    } finally {
      clearTimeout(deadline);
      admitted.release();
    }
  });

  // ── legacy permalinks: registered last, so every named route above wins ──
  app.get("/:date/translate", (c) => {
    const date = c.req.param("date");
    if (!isValidDate(date)) return page404(c);
    const lang = validQueryLang(c.req.query("lang"));
    return redirect(c, `/issues/${date}/translate${lang ? `?lang=${lang}` : ""}`, 308);
  });
  app.get("/:date", (c) => {
    const raw = c.req.param("date");
    // The .md suffix is kept out of the date check and carried across, for old /2026-07-03.md links.
    const [bare, suffix] = raw.endsWith(".md") ? [raw.slice(0, -3), ".md"] : [raw, ""];
    if (!isValidDate(bare)) return page404(c);
    return redirect(c, `/issues/${bare}${suffix}`, 308);
  });

  app.notFound((c) => {
    const path = c.req.path.length > 1 ? c.req.path.replace(/\/+$/, "") : c.req.path;
    const allowed = METHODS.find(([re]) => re.test(path))?.[1];
    if (allowed && !allowed.split(",").includes(c.req.method)) return c.body(null, 405, { allow: allowed });
    return page404(c);
  });
  return app;
}

// Scheme and authority this request came in on, for an absolute URL when no domain is configured. An
// allow-list of authority characters: the value lands in an href. Empty without a usable Host.
export function requestOrigin(host: string | undefined, forwardedProto: string | undefined): string {
  const h = host?.trim();
  if (!h || h.length > 255 || !/^[A-Za-z0-9.\-:[\]_]+$/.test(h)) return "";
  const local = h.startsWith("localhost") || h.startsWith("127.") || h.startsWith("[::1]") || h.endsWith(".local") || h.includes(".local:");
  const scheme = forwardedProto?.split(",")[0]?.trim() || (local ? "http" : "https");
  return `${scheme}://${h}`;
}
