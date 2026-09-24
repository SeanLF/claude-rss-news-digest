import { type Context, Hono } from "hono";
import { type ArchivePage, biasMap, fetchArchive, fragmentHtml, DEFAULT_LIMIT } from "./archive.js";
import { type Assets, FAVICON_SVG } from "./assets.js";
import { type SiteConfig, baseUrl, subscriptionsEnabled } from "./config.js";
import type { SiteData } from "./data.js";
import { FEED_ENTRY_LIMIT, atomFeed } from "./feed.js";
import { htmlLinkHeader, indexMarkdown, issueMarkdown, markdownLinkHeader, negotiate, hiddenPointer } from "./markdown.js";
import { NOTICES, type IndexScope, indexPage } from "./pages/index.js";
import { issuePage } from "./pages/issue.js";
import type { PageCtx } from "./pages/chrome.js";
import { feedbackPage, notFoundPage, searchPage, sourcesPage, statsPage } from "./pages/sub.js";
import { threadPage, threadsFragment, threadsPage } from "./pages/threads.js";
import { RateLimiter, clientKey } from "./ratelimit.js";
import { securityHeaders } from "./security.js";
import { type CatalogueEntry, sourceRows } from "./sources.js";
import { computeMetrics, statsFrom, statsJson, statsValue } from "./stats.js";
import { type Mail, CONFIRM_TTL_S, addContact, isValidEmail, makeToken, sendConfirmation, verifyToken } from "./subscribe.js";
import { OLDER_PAGE, threadDetail, threadIndex } from "./threads.js";
import { SEARCH_LIMIT, isValidDate, sanitizeQuery } from "./text.js";
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
  now: () => Date;
}

const md = (c: Context, body: string, link: string, status = 200) =>
  c.body(body, status as 200, { "content-type": "text/markdown; charset=utf-8", vary: "accept", link });
const text = (c: Context, body: string, status: number) => c.body(body, status as 200, { "content-type": "text/plain; charset=utf-8" });
const notAcceptable = (c: Context) => c.body("Not Acceptable — this URL is available as text/html or text/markdown.\n", 406, { "content-type": "text/plain; charset=utf-8", vary: "accept" });
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
  [/^\/subscribe$/, "POST"],
  [/^\/(|confirm|privacy|health|favicon\.ico|robots\.txt|llms\.txt|llms-full\.txt|index\.md|apple-touch-icon(-precomposed)?\.png|og-image\.png|sources|feed\.xml|stats|stats\.json|archive|threads|threads\/more|search|feedback|today|translate|today\/translate)$/, "GET,HEAD"],
  [/^\/(thread|issues)\/[^/]+$/, "GET,HEAD"],
  [/^\/issues\/[^/]+\/translate$/, "GET,HEAD"],
  [/^\/assets\/fonts\/[^/]+$/, "GET,HEAD"],
  [/^\/[^/]+(\/translate)?$/, "GET,HEAD"],
];

// The largest request body any route reads: the subscribe form is one address, far below this.
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

export function siteApp(deps: SiteDeps): Hono {
  const { cfg, assets, catalogue, data } = deps;
  const base = baseUrl(cfg);
  const link = (p: string) => `${base.replace(/\/+$/, "")}${p}`;
  const bias = biasMap(catalogue);
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
  // Read here, once, so a client that hangs up mid-body is a 400 before any handler runs, not a 500
  // out of a handler.
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
    let body = `# ${cfg.digestName}\n\n> An automated daily news briefing on geopolitics, tech, and privacy. It reads feeds across five continents, clusters the day's stories, writes a bias-labelled digest, fact-checks itself against the sources, and sends. Curated and written by Claude; no human edits any issue.\n\n## Read the briefing\n\n`;
    body += `- [Latest issue](${link("/today")}): redirects to the newest dated issue; append \`.md\` or send \`Accept: text/markdown\` for Markdown.\n`;
    body += `- [Archive index](${link("/index.md")}): every issue as Markdown, newest first, each linking to its \`.md\`.\n`;
    body += "\n## Reference\n\n";
    body += `- [Sources and bias ratings](${link("/sources")}): every source with its Media Bias/Fact Check bias and factuality rating.\n`;
    body += `- [Transparency stats](${link("/stats")}) ([JSON](${link("/stats.json")})): subscriber count, source-spectrum balance, and AI cost per issue.\n`;
    if (cfg.sourceUrl) body += `- [Source code](${cfg.sourceUrl})\n`;
    body += "\nEvery dated issue at `/issues/YYYY-MM-DD` also serves Markdown at `/issues/YYYY-MM-DD.md`.\n";
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
  app.get("/subscribe", (c) => c.body(null, 405, { allow: "POST" }));
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
