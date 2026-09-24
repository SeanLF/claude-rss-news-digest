import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { atomFeed } from "./feed.js";
import { negotiate } from "./markdown.js";
import { computeMetrics, pct3, jsd3, statsFrom, statsJson, statsValue } from "./stats.js";
import { loadCatalogue, sourceRows, websiteFromRss } from "./sources.js";
import { makeToken, verifyToken } from "./subscribe.js";
import { brandHtml, escapeHtml, formatDate, formatDayMonthYear, isValidDate } from "./text.js";
import { citedIds, factsFrom, threadDetail } from "./threads.js";
import { pickTargetLang, proxyHost, validQueryLang, validTranslatePath } from "./translate.js";
import { SOURCES_FILE, fakeData } from "./testing.js";

describe("text", () => {
  it("formats and validates dates as circulation did", () => {
    expect([formatDate("2026-01-24"), formatDate("2026-13-01"), formatDayMonthYear("2026-02-06")]).toEqual(["Saturday, January 24", "2026-13-01", "6 Feb 2026"]);
    expect(["2026-01-24", "2026-1-24"].every(isValidDate)).toBe(true);
    expect(["2026-00-15", "2026-01-32", "01-24-2026", "../etc/passwd", "2026-01-24; DROP TABLE", ""].some(isValidDate)).toBe(false);
    expect(escapeHtml(`<a href="x">it's & </a>`)).toBe("&lt;a href=&quot;x&quot;&gt;it&#x27;s &amp; &lt;/a&gt;");
    expect(brandHtml("News Digest")).toBe("News <em>Digest</em>");
  });
});

describe("the confirmation token", () => {
  // Computed outside either implementation (Python hmac over the same payload): a link the Rust server
  // mailed must still confirm.
  const VECTOR = "cmVhZGVyQGdtYWlsLmNvbQoyMDAw.DqdrQT-AEjKtDOxD8rXgJ1hhhVIbS30RaZOrb4hWvko";
  it("keeps the Rust wire format", () => {
    expect(makeToken("top-secret-key", "reader@gmail.com", 2000)).toBe(VECTOR);
    expect(verifyToken("top-secret-key", VECTOR, 1000)).toBe("reader@gmail.com");
  });
  it.each([
    ["expired (exp is exclusive)", "top-secret-key", VECTOR, 2000],
    ["under another secret", "other-secret", VECTOR, 1000],
    ["with a forged payload", "top-secret-key", `${Buffer.from("attacker@evil.com\n2000").toString("base64url")}.${VECTOR.split(".")[1]}`, 1000],
    ["with a forged signature", "top-secret-key", `${VECTOR.split(".")[0]}.${Buffer.from("garbage").toString("base64url")}`, 1000],
    ["malformed", "top-secret-key", "no-dot-here", 1000],
    ["with extra dots", "top-secret-key", `${VECTOR}.x`, 1000],
  ])("refuses a token %s", (_what, secret, token, now) => {
    expect(verifyToken(secret, token, now)).toBeUndefined();
  });
});

describe("translate", () => {
  it("picks the reader's first non-English language, never English", () => {
    expect(pickTargetLang("en-US,en;q=0.9,fr;q=0.8")).toBe("fr");
    expect(pickTargetLang("pt-BR")).toBe("pt-BR");
    expect(pickTargetLang("en-CA,en-GB")).toBe("fr");
    expect(pickTargetLang("<script>,de")).toBe("de");
    expect(pickTargetLang(undefined)).toBe("fr");
    expect([validQueryLang("en-GB"), validQueryLang("fr"), validQueryLang("a b")]).toEqual([undefined, "fr", undefined]);
  });
  it("sends only same-origin paths through the proxy", () => {
    expect(["/stats", "/thread/12"].map(validTranslatePath)).toEqual(["/stats", "/thread/12"]);
    expect(["//evil.example", "https://x", "/a?b", "/a%2F", "x"].map(validTranslatePath).every((p) => p === undefined)).toBe(true);
    expect(proxyHost("news-digest.seanfloyd.dev")).toBe("news--digest-seanfloyd-dev.translate.goog");
  });
});

describe("Accept negotiation", () => {
  it.each([
    [undefined, "html"],
    ["*/*", "html"],
    ["text/html, text/markdown;q=0.5", "html"],
    ["text/markdown, text/html", "markdown"],
    ["text/*", "html"],
    ["text/markdown", "markdown"],
    ["application/json", "not-acceptable"],
    ["text/markdown;q=bogus, text/html;q=0.1", "html"],
    ["text/markdown;q=2, text/html", "markdown"],
  ])("%s -> %s", (accept, want) => {
    expect(negotiate(accept)).toBe(want);
  });
});

describe("sources and stats", () => {
  const cat = loadCatalogue(SOURCES_FILE);
  const parked = cat.filter((s) => !s.active).map((s) => s.id);

  it("links outlets, not feeds", () => {
    expect(websiteFromRss("https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en", "Reuters")).toBe("https://reuters.com");
    expect(websiteFromRss("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World")).toBe("https://www.bbc.com");
    expect(websiteFromRss("https://rss.example.org/feed", "X")).toBe("https://example.org");
  });

  it("lists today's shelf: a parked source is off the sources page", () => {
    expect(parked.length).toBeGreaterThan(0);
    const names = sourceRows(cat).map((r) => r.name);
    for (const id of parked) expect(names).not.toContain(cat.find((s) => s.id === id)!.name);
  });

  it("drops a parked source from the health surfaces but keeps its history in the shipped figures", () => {
    const data = {
      sourceHealth: [{ sourceId: parked[0]!, total: 30, successes: 0 }, { sourceId: "reuters", total: 30, successes: 30 }],
      sourceUsage: cat.map((s) => ({ sourceId: s.id, tier: "must_know", count: 1 })),
      recentRuns: [],
      dedup: { count: 0, avg: null, min: null, max: null },
      neverSelected: [parked[0]!],
      cost: { runs: 0, keptTotal: 0, costTotal: 0, shippedTotal: 0, recipientsLatest: 0 },
    };
    const s = statsFrom(data, 30, cat);
    expect(s.health.map((h) => h.sourceId)).toEqual(["reuters"]);
    expect(s.neverSelected).toEqual([]);
    const m = computeMetrics(s, cat);
    expect(m.totalShipped).toBe(cat.length);
    expect(m.sourcesUsed).toBeLessThanOrEqual(m.catalogTotal);
    expect(m.coveragePct).toBeLessThanOrEqual(100);
  });

  it("sums percentages to 100 and bounds the divergence", () => {
    expect(pct3([1, 1, 1]).reduce((a, b) => a + b)).toBe(100);
    expect(pct3([0, 0, 0])).toEqual([0, 0, 0]);
    expect(jsd3([3, 5, 2], [30, 50, 20])).toBeCloseTo(0, 12);
    expect(jsd3([1, 0, 0], [0, 1, 0])).toBeCloseTo(1, 12);
  });

  it("serialises stats as serde_json did: keys sorted, whole floats as floats", () => {
    const s = statsFrom({ sourceHealth: [{ sourceId: "a", total: 2, successes: 2 }], sourceUsage: [], recentRuns: [{ runAt: "2026-09-23 10:25:54", articlesKept: 1, recipients: 2, apiCostUsd: 5 }], dedup: { count: 0, avg: null, min: null, max: null }, neverSelected: [], cost: { runs: 0, keptTotal: 0, costTotal: 0, shippedTotal: 0, recipientsLatest: 0 } }, 30, []);
    expect(statsJson(statsValue(s))).toBe(
      '{"dedup_stats":null,"never_selected":[],"period_days":30,"recent_runs":[{"api_cost_usd":5.0,"articles_emailed":2,"articles_kept":1,"run_at":"2026-09-23 10:25:54"}],"source_health":[{"source_id":"a","success_rate_pct":100.0,"successes":2,"total_fetches":2}],"source_usage":[]}',
    );
  });
});

const chain = (links: Record<number, number | null>) =>
  fakeData({ mergedInto: async (id) => (id in links ? links[id]! : undefined), thread: async (id) => ({ label: `t${id}`, status: "active", installments: [], openQuestions: [] }) });

describe("threads", () => {
  it("scrubs article ids from facts and grounds questions on the raising run's ids", () => {
    const content = JSON.stringify({ whats_new: [{ fact: "Talks resume [A3].", sources: ["A3"] }, { fact: "According to A9.", sources: ["A9"] }, { fact: "Third." }, { fact: "Fourth." }], cited_ids: ["A1"] });
    expect(factsFrom(content)).toEqual(["Talks resume.", "Third.", "Fourth."]);
    expect(citedIds(content)).toEqual(["A1"]);
    expect(citedIds(JSON.stringify({ whats_new: [{ sources: ["A2", " "] }] }))).toEqual(["A2"]);
    expect(citedIds("not json")).toEqual([]);
  });

  it("follows a merge chain to its survivor, and refuses a loop or one too long", async () => {
    expect(await threadDetail(chain({ 1: 2, 2: null }), 1)).toMatchObject({ label: "t2", mergedInto: 2 });
    expect(await threadDetail(chain({ 1: 1 }), 1)).toBeUndefined();
    const long = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [i + 1, i + 2]));
    expect(await threadDetail(chain({ ...long, 11: null }), 1)).toBeUndefined();
    expect(await threadDetail(chain({}), 5)).toBeUndefined();
  });
});

describe("the feed", () => {
  it("keeps circulation's entry ids and escapes what the database holds", () => {
    const xml = atomFeed("News & Digest", "https://digest.example", [{ date: "2026-06-12", preheader: "A <b>day</b>" }]);
    expect(xml).toContain("<id>https://digest.example/issues/2026-06-12</id>");
    expect(xml).toContain("<summary>A &lt;b&gt;day&lt;/b&gt;</summary>");
    expect(xml).toContain("<title>News &amp; Digest – Friday, June 12</title>");
    expect(atomFeed("N", "", [])).toContain("<updated>1970-01-01T00:00:00Z</updated>");
  });
});

const src = new URL("..", import.meta.url).pathname;
const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(dir, e.name)) : e.name.endsWith(".ts") ? [join(dir, e.name)] : []));
// Every module specifier: `from "x"`, side-effect `import "x"`, and `import("x")`.
const imports = (f: string) => [...readFileSync(f, "utf8").matchAll(/(?:from|import)\s*\(?\s*"([^"]+)"/g)].map((m) => m[1]!);
// The production modules the site reaches, following relative imports transitively (tests excluded).
function reachable(entries: string[]): Map<string, string[]> {
  const seen = new Map<string, string[]>();
  const queue = entries.map((f) => [f, [f]] as [string, string[]]);
  while (queue.length) {
    const [f, path] = queue.shift()!;
    if (seen.has(f)) continue;
    seen.set(f, path);
    for (const i of imports(f)) {
      if (!i.startsWith(".")) continue;
      const target = join(f, "..", i.replace(/\.js$/, ".ts"));
      if (!seen.has(target)) queue.push([target, [...path, target]]);
    }
  }
  return seen;
}
const PIPELINE = /\/(workflow|activities|runner|fulltext|gate|mail|ops)\/|@temporalio|claude-agent-sdk/;

describe("the site's boundary", () => {
  // The site runs as its own process and must not die with the pipeline: it imports no workflow,
  // activity, runner or SDK of the pipeline's, and nothing of the pipeline's imports it.

  it("reaches none of the pipeline's machinery, directly or through a shared module", () => {
    const mods = reachable(files(join(src, "site")).filter((f) => !f.endsWith(".test.ts")));
    const bad = [...mods].flatMap(([f, path]) => [...(PIPELINE.test(f) ? [f] : []), ...imports(f).filter((i) => PIPELINE.test(i))].map((i) => `${path.join(" -> ")}: ${i}`));
    expect(bad).toEqual([]);
    // Negative control: the walk does reach shared modules outside site/.
    expect([...mods.keys()].some((f) => f.endsWith("/contracts/leaks.ts"))).toBe(true);
  });

  it("is imported by nothing outside it but its CLIs", () => {
    const bad = files(src)
      .filter((f) => !f.includes("/site/") && !f.endsWith("/cli/site-parity.ts") && !f.endsWith("/cli/search-eval.ts"))
      .flatMap((f) => imports(f).filter((i) => i.includes("/site/")).map((i) => `${f}: ${i}`));
    expect(bad).toEqual([]);
  });
});
