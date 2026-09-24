import type { SearchHit } from "../data.js";
import { type Metrics, type Stats } from "../stats.js";
import { type SourceRow, bucket } from "../sources.js";
import { escapeHtml, formatDayMonthYear } from "../text.js";
import { askCss, askJs, connectCss, copyJs, feedbackCss, notFoundCss, searchCss, sourcesCss, statsCss } from "./blobs.js";
import { type PageCtx, brand, pageBody, pageHead, script, subChrome, subMasthead } from "./chrome.js";

// The sub-pages (circulation's templates/*.rs): sources, search, stats, feedback, connect, ask and the
// friendly 404, each in the shared frame.

const name = (ctx: PageCtx) => escapeHtml(ctx.cfg.digestName);
const TAG_ALL = "An automated daily briefing. Curated and fact-checked by Claude; no human edits any issue. &copy; Sean Floyd";

// ── 404 ──
export function notFoundPage(ctx: PageCtx, heading: string, message: string): string {
  const chrome = subChrome(ctx.cfg, "", "/", "Lost? Every issue is one tap from the archive.");
  const inner = `<main id="main" class="narrow">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <p class="nf-code">404</p>
      <h1 class="h1">${heading}</h1>
      <p class="lede">${message}</p>
      <p class="body">The link may be old, or the page may have moved. Here's the way back:</p>
      <div class="ways">
        <a class="primary" href="/">&larr; The archive</a>
        <a href="/today">Today's issue</a>
        <a href="/search">Search</a>
      </div>
    </main>`;
  return pageHead(ctx, name(ctx), message, notFoundCss) + pageBody(ctx, chrome, inner);
}

// ── sources ──
function factualityMeter(f: string): string {
  const [on, label] = (
    {
      "very-high": [3, "Very high"],
      high: [2, "High"],
      // MBFC's Mostly Factual sits between High and Mixed: High's bars, its own label.
      "mostly-factual": [2, "Mostly factual"],
      mixed: [1, "Mixed"],
      low: [1, "Low"],
      "very-low": [0, "Very low"],
    } as Record<string, [number, string]>
  )[f] ?? [0, "Unrated"];
  const bars = [1, 2, 3].map((i) => (i <= on ? "<i></i>" : '<i class="off"></i>')).join("");
  return `<span class="fact"><span class="meter">${bars}</span><span class="fl">${label}</span></span>`;
}
function sourceRow(s: SourceRow): string {
  const feeds = s.feedCount > 1 ? `<span class="feeds"> &middot; ${s.feedCount} feeds</span>` : "";
  return `<li><a class="row" href="${escapeHtml(s.website)}"><span><span class="nm">${escapeHtml(s.name)}${feeds}</span><span class="persp">${escapeHtml(s.perspective)}</span></span>${factualityMeter(s.factuality)}</a></li>`;
}
function biasSection(rows: SourceRow[], key: string, anchor: string, label: string): string {
  if (!rows.length) return "";
  return `<section class="sec" id="${anchor}"><div class="sec-h"><span class="dot ${key}" aria-hidden="true"></span><h2>${label}</h2><span class="ct">${rows.length} outlets</span></div><div class="colhead"><span>Source</span><span class="r">Factuality</span></div><ul class="srcs">${rows.map(sourceRow).join("")}</ul></section>`;
}
const lab = (n: number, anchor: string, label: string): string => (n > 0 ? `<a class="on" href="#${anchor}">${label}</a>` : `<span>${label}</span>`);
export function sourcesPage(ctx: PageCtx, sources: SourceRow[]): string {
  const left = sources.filter((s) => bucket(s.bias) === "l");
  const centre = sources.filter((s) => bucket(s.bias) === "c");
  const right = sources.filter((s) => bucket(s.bias) === "r");
  const feeds = sources.reduce((n, s) => n + s.feedCount, 0);
  const chrome = subChrome(ctx.cfg, "sources", "/sources", "Bias &amp; factuality ratings via Media Bias/Fact Check; each row links to the outlet.");
  const spectrum = `<div class="spectrum">
      <div class="spec-counts" aria-hidden="true"><span></span><span></span><span>${left.length}</span><span>${centre.length}</span><span>${right.length}</span><span></span><span></span></div>
      <div class="spec-bar2" role="img" aria-label="Bias distribution: ${left.length} lean-left, ${centre.length} centre, ${right.length} lean-right outlets; none far-left or far-right">
        <span class="z e-l"></span><span class="z e-l"></span><span class="z on-l"></span><span class="z on-c"></span><span class="z on-r"></span><span class="z e-r"></span><span class="z e-r"></span>
      </div>
      <div class="spec-labels"><span>Far left</span><span>Left</span>${lab(left.length, "lean-left", "Lean left")}${lab(centre.length, "centre", "Centre")}${lab(right.length, "lean-right", "Lean right")}<span>Right</span><span>Far right</span></div>
      <p class="spec-cap"><b style="color:var(--ink2);font-weight:600;">${sources.length} outlets</b> across the spectrum &mdash; none at the far-left or far-right extremes.</p>
    </div>`;
  const method =
    '<p class="method">Bias and factual-reporting ratings come from <a href="https://mediabiasfactcheck.com">Media Bias/Fact Check</a>, read per outlet rather than aggregated, so every rating here is traceable to one published assessment. Ratings describe the outlet, not the individual article. The digest draws from across the political spectrum to show how outlets cover the same story differently. Perspective notes each outlet\'s vantage point.</p>';
  const inner = `${subMasthead(brand(ctx), "Sources", "Bias &amp; factuality &middot; rated by Media Bias/Fact Check", `<b>${sources.length}</b> outlets &middot; <b>${feeds}</b> feeds &middot; across the spectrum`)}
    <main id="main">
    ${method}
    ${spectrum}
    ${biasSection(left, "l", "lean-left", "Lean left")}${biasSection(centre, "c", "centre", "Centre")}${biasSection(right, "r", "lean-right", "Lean right")}
    </main>`;
  return pageHead(ctx, name(ctx), "News sources by bias and factuality — the catalog behind the digest.", sourcesCss) + pageBody(ctx, chrome, inner);
}

// ── search ──
const tierParts = (tier: string): [string, string] =>
  tier === "must_know" ? ["must", "Must Know"] : tier === "should_know" ? ["should", "Should Know"] : ["should", tier === "" ? "" : escapeHtml(tier)];
function resultRow(r: SearchHit): string {
  const [cls, label] = tierParts(r.tier);
  const head = escapeHtml(r.headline);
  return r.date
    ? `<li class="result"><a href="/issues/${r.date}"><span class="r-date">${formatDayMonthYear(r.date)}</span><span class="r-tier ${cls}">${label}</span><span class="r-head">${head}</span></a></li>`
    : `<li class="result unlinked"><span class="r-tier ${cls}">${label}</span><span class="r-head">${head}</span></li>`;
}
export function searchPage(ctx: PageCtx, query: string | undefined, results: SearchHit[]): string {
  const chrome = subChrome(ctx.cfg, "", "/search", "Search covers all published headlines. Full-text over each issue's stories.");
  let body: string;
  if (query === undefined) body = '<p class="note">Search matches wording in every published headline. Enter a term above.</p>';
  else if (!results.length)
    body = `<p class="rescount">No results for &ldquo;${escapeHtml(query)}&rdquo;.</p><p class="note">Search matches headline wording — try a broader or differently-worded term.</p>`;
  else
    body = `<p class="rescount"><b>${results.length}</b> results for &ldquo;${escapeHtml(query)}&rdquo;</p><ol class="results">${results.map(resultRow).join("")}</ol><p class="note">Results match headlines across every past issue; each opens that day's digest.</p>`;
  const inner = `<header class="masthead">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <h1 class="h1">Search the archive</h1>
      <form class="searchform" role="search" action="/search" method="get">
        <input class="searchfield" type="search" name="q" value="${escapeHtml(query ?? "")}" placeholder="Search past headlines&hellip;" aria-label="Search past headlines">
        <button class="searchbtn" type="submit">Search</button>
      </form>
    </header>
    <main id="main">
    ${body}
    </main>`;
  return pageHead(ctx, name(ctx), "Search every published headline in the digest archive.", searchCss) + pageBody(ctx, chrome, inner);
}

// ── stats ──
const rag = (rate: number): [string, string] => (rate >= 95 ? ["ok", "✓"] : rate >= 80 ? ["warn", "▲"] : ["bad", "✕"]);
const tile = (value: string, cls: string, label: string, sub: string): string =>
  `<span class="st"><span class="v ${cls}">${value}</span><span class="l">${label}</span>${sub ? `<span class="s">${sub}</span>` : ""}</span>`;
const spectrumRow = (label: string, p: [number, number, number], aria: string): string =>
  `<div class="balrow"><span class="rl">${label}</span><div class="barwrap"><div class="sbar" role="img" aria-label="${aria}"><span class="on-l" style="width:${p[0]}%"></span><span class="on-c" style="width:${p[1]}%"></span><span class="on-r" style="width:${p[2]}%"></span></div><div class="bkeys"><span class="bkey" style="width:${p[0]}%">${p[0]}%</span><span class="bkey" style="width:${p[1]}%">${p[1]}%</span><span class="bkey" style="width:${p[2]}%">${p[2]}%</span></div></div></div>`;
const fixed = (x: number, d: number): string => x.toFixed(d);

const aria = (w: string, p: [number, number, number]): string => `${w} mix: ${p[0]}% lean-left, ${p[1]}% centre, ${p[2]}% lean-right`;
function balance(m: Metrics): string {
  const [verdict, cls] = m.jsd < 0.05 ? ["Tracks catalog", "ok"] : m.jsd < 0.15 ? ["Slight lean", "warn"] : ["Skews the shelf", "bad"];
  const tiles =
    tile(verdict, cls, "Selection vs shelf", `<span title="Jensen–Shannon divergence: distance from shipped mix to the catalog (0 = identical)">JSD</span> ${fixed(m.jsd, 2)} — curation isn't the skew`) +
    tile(`${m.bucketsSourced} of 7`, "warn", "Spectrum buckets sourced", "left and right fold into lean-*") +
    tile(`${m.factualityHighPct}%`, m.factualityHighPct >= 90 ? "ok" : "warn", "Shipped ≥ high factuality", "MBFC High or Very High");
  return `<section>
  <div class="sec-h"><h2>Balance</h2><span class="ct">shipped vs catalog</span></div>
  <div class="stats">${tiles}</div>
  <div class="bal">
    ${spectrumRow("Shipped", m.shippedPct, aria("Shipped", m.shippedPct))}
    ${spectrumRow("Catalog", m.catalogPct, aria("Catalog", m.catalogPct))}
    <p class="ballegend"><span class="k k-l"><span class="sw"></span>Lean left</span><span class="k k-c"><span class="sw"></span>Centre</span><span class="k k-r"><span class="sw"></span>Lean right</span></p>
  </div>
  <p class="note">The two bars compare the shipped mix against the catalog it draws from — they track closely, so the digest is balanced <em>relative to its shelf</em>. Three buckets appear because left and right <b style="color:var(--ink2);font-weight:600;">fold into lean-left and lean-right</b> here; the catalog does hold Left-rated outlets. What it holds none of is far-left or far-right, and that is a <b style="color:var(--ink2);font-weight:600;">curation choice, not a quality law</b> — plenty of strongly-slanted outlets report accurately.</p>
</section>`;
}
function concentration(m: Metrics, never: string[], days: number): string {
  const [hhiCls, hhiDesc] = m.hhi < 0.15 ? ["ok", "low — well spread"] : m.hhi < 0.25 ? ["warn", "moderate"] : ["bad", "concentrated"];
  const top = m.topSources[0];
  const tiles =
    tile(`${m.sourcesUsed}<span style="color:var(--muted);font-size:18px;"> / ${m.catalogTotal}</span>`, "", "Sources used", `${m.coveragePct}% catalog coverage`) +
    tile(fixed(m.hhi, 2), hhiCls, 'Concentration <span title="Herfindahl–Hirschman Index: how concentrated sourcing is; low = well spread">(HHI)</span>', `${hhiDesc} · eff. ${fixed(m.effectiveN, 0)} sources`) +
    tile(top ? `${fixed(top.sharePct, 0)}%` : "—", "", "Top source share", top ? escapeHtml(top.name) : "");
  const shares = m.topSources
    .map((s) => `<div class="share"><span class="sn">${escapeHtml(s.name)}</span><span class="track"><span class="fill" style="width:${fixed(s.barPct, 0)}%"></span></span><span class="pc">${fixed(s.sharePct, 0)}%</span></div>`)
    .join("");
  const drill = never.length ? `<p class="drill">Never used in ${days} days (${never.length}): ${never.map((s) => `<code>${escapeHtml(s)}</code>`).join(", ")}.</p>` : "";
  return `<section>
  <div class="sec-h"><h2>Concentration &amp; coverage</h2><span class="ct">${m.totalShipped} source picks · ${days} days</span></div>
  <div class="stats">${tiles}</div>
  <div class="shares">${shares}</div>
  ${drill}
</section>`;
}
function health(s: Stats, names: Map<string, string>): string {
  const n = { ok: 0, warn: 0, bad: 0 };
  // Worst first: problems at the top.
  const rows = s.health
    .toSorted((a, b) => a.ratePct - b.ratePct)
    .map((h) => {
      const [cls, ic] = rag(h.ratePct);
      n[cls as keyof typeof n]++;
      return `<tr><td class="src">${escapeHtml(names.get(h.sourceId) ?? h.sourceId)}</td><td class="n">${h.total}</td><td class="rate-cell"><span class="rate ${cls}"><span class="ic">${ic}</span>${fixed(h.ratePct, 0)}%</span></td></tr>`;
    })
    .join("");
  return `<section>
  <div class="sec-h"><h2>Source health</h2><span class="ct">${n.ok} healthy · ${n.warn} degraded · ${n.bad} down</span></div>
  <div class="tbl-wrap"><table><thead><tr><th scope="col">Source</th><th scope="col" class="n">Fetches</th><th scope="col" class="n">Success</th></tr></thead><tbody>${rows || '<tr><td class="src" colspan="3">No fetch records in this window.</td></tr>'}</tbody></table></div>
</section>`;
}
function cost(s: Stats): string {
  const c = s.cost;
  const perRun = c.runs > 0 ? c.costTotal / c.runs : 0;
  const perSub = c.recipientsLatest > 0 ? perRun / c.recipientsLatest : 0;
  // Two questions, two tiles: what a reader's story costs (~16 a run) and what curation reads (~550).
  const perStory = c.shippedTotal > 0 ? c.costTotal / c.shippedTotal : 0;
  const perArticle = c.keptTotal > 0 ? c.costTotal / c.keptTotal : 0;
  const tiles =
    tile(`$${fixed(perRun, 2)}`, "", "Cost / run", "API list-price equiv.") +
    tile(`$${fixed(perSub, 3)}`, "", "Cost / subscriber", `${c.recipientsLatest} recipients`) +
    tile(`$${fixed(perStory, 3)}`, "", "Cost / shipped story", `${c.shippedTotal} stories readers saw`) +
    tile(`$${fixed(perArticle, 4)}`, "", "Cost / article ingested", `${c.keptTotal} read by curation`);
  const rows = s.recentRuns
    .map(
      (r) =>
        `<tr><td class="time">${escapeHtml(r.runAt.slice(0, 16))}</td><td class="n">${r.articlesKept}</td><td class="n">${r.recipients}</td><td class="n">${r.apiCostUsd === null ? "—" : `$${fixed(r.apiCostUsd, 2)}`}</td></tr>`,
    )
    .join("");
  return `<section>
  <div class="sec-h"><h2>Cost &amp; reach</h2><span class="ct">per run</span></div>
  <div class="stats">${tiles}</div>
  <div class="tbl-wrap"><table><thead><tr><th scope="col">Filed (UTC)</th><th scope="col" class="n">Kept</th><th scope="col" class="n">Recipients</th><th scope="col" class="n">Cost</th></tr></thead><tbody>${rows || '<tr><td class="time" colspan="4">No completed runs in this window.</td></tr>'}</tbody></table></div>
</section>`;
}
function geographic(m: Metrics): string {
  if (!m.regions.length) return "";
  const total = m.regions.reduce((a, [, c]) => a + c, 0);
  const top = Math.max(m.regions[0]?.[1] ?? 1, 1);
  const rows = m.regions
    .map(([n, c]) => `<div class="share"><span class="sn">${n}</span><span class="track"><span class="fill" style="width:${fixed((c / top) * 100, 0)}%"></span></span><span class="pc">${fixed(total > 0 ? (c / total) * 100 : 0, 0)}%</span></div>`)
    .join("");
  const topPct = total > 0 ? Math.round(((m.regions[0]?.[1] ?? 0) / total) * 100) : 0;
  const tiles = tile(fixed(m.geoEffective, 1), "", "Effective regions", `of ${m.regions.length} · geo-HHI ${fixed(m.geoHhi, 2)}`) + tile(m.regions[0]?.[0] ?? "", "", "Top region", `${topPct}% of stories`);
  return `<section>
  <div class="sec-h"><h2>Geographic origin</h2><span class="ct">source vantage, not story location</span></div>
  <div class="stats">${tiles}</div>
  <div class="shares">${rows}</div>
  <p class="note">Region reflects each <em>source's</em> vantage point, not where the story happened. The catalog skews Western — true story-geography would need per-story geo-tagging.</p>
</section>`;
}
export function statsPage(ctx: PageCtx, days: number, s: Stats, m: Metrics, names: Map<string, string>): string {
  const chrome = subChrome(ctx.cfg, "stats", "/stats", "Balance = shipped source-bias mix vs. the catalog. Cost is API list-price equiv., not billed spend.");
  const opt = (d: number, label: string) => `<a href="?days=${d}" aria-current="${d === days}">${label}</a>`;
  const toggle = `<div class="toolbar"><div class="seg" role="group" aria-label="Reporting period">${opt(7, "7 days")}${opt(30, "30 days")}${opt(90, "90 days")}</div></div>`;
  const inner = `${subMasthead(brand(ctx), "Stats", `Editorial health &middot; last ${days} days`, `<b>${s.cost.runs}</b> runs &middot; <b>${s.cost.recipientsLatest}</b> subscribers &middot; <b>$${fixed(s.cost.costTotal, 2)}</b> API-equiv.`)}
    ${toggle}
    <main id="main">
    ${balance(m)}
    ${geographic(m)}
    ${concentration(m, s.neverSelected, days)}
    ${health(s, names)}
    ${cost(s)}
    </main>`;
  return pageHead(ctx, name(ctx), "How the digest is performing: balance, source health, cost, and coverage.", statsCss) + pageBody(ctx, chrome, inner);
}

// ── feedback ──
const HELPS = [
  "A story that felt one-sided, or a source you'd trust more.",
  "Something important the digest missed entirely.",
  "Whether the length and cadence work for your mornings.",
  "Anything that made you want to unsubscribe — those are the most valuable of all.",
];
export function feedbackPage(ctx: PageCtx): string {
  const chrome = subChrome(ctx.cfg, "", "/feedback", "No form, no tracking — feedback goes straight to a human inbox.");
  const mail = ctx.cfg.contactEmail ? `<a class="btn primary" href="mailto:${escapeHtml(ctx.cfg.contactEmail)}?subject=Digest%20feedback">&#9993; Email your feedback</a>` : "";
  const inner = `<main id="main" class="narrow">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <h1 class="h1">Tell me what you <em>think</em></h1>
      <p class="lede">This digest is a work in progress, and the best version of it is shaped by the people who read it every morning.</p>
      <p class="body">Reply to any issue in your inbox, or send a note directly — a real person reads every one, and it genuinely steers what gets built next.</p>
      <div class="cta">${mail}<span class="alt">or just <a href="/today">reply to today's issue &rarr;</a></span></div>
      <div class="helps">
        <h2>Especially useful to hear</h2>
        <ul>${HELPS.map((h) => `<li>${h}</li>`).join("")}</ul>
      </div>
    </main>`;
  return pageHead(ctx, name(ctx), "Tell me what you think — feedback goes straight to a human inbox.", feedbackCss) + pageBody(ctx, chrome, inner);
}

// ── connect ──
export const SERVER_KEY = "news-digest";
export const cursorLink = (mcpUrl: string): string =>
  `cursor://anysphere.cursor-deeplink/mcp/install?name=${SERVER_KEY}&config=${Buffer.from(`{"url":"${mcpUrl}"}`).toString("base64")}`;
// Every command is derived from the one MCP URL, so a domain change cannot leave a stale one.
export const commands = (mcpUrl: string): [string, string, string][] => [
  ["Claude Code", "Run in your terminal", `claude mcp add --transport http ${SERVER_KEY} ${mcpUrl}`],
  ["Codex", "Run in your terminal", `codex mcp add ${SERVER_KEY} --url ${mcpUrl}`],
  // VS Code's own documented quoting, which cmd.exe also passes through as JSON.
  ["VS Code", "Run in your terminal", `code --add-mcp "{\\"name\\":\\"${SERVER_KEY}\\",\\"type\\":\\"http\\",\\"url\\":\\"${mcpUrl}\\"}"`],
  ["Any other client", "Paste this URL", mcpUrl],
];
export function connectPage(ctx: PageCtx, origin: string, tools: { name: string; description: string }[]): string {
  const chrome = subChrome(ctx.cfg, "", "/connect", "Read-only tools over the archive, for any MCP client.");
  const mcpUrl = `${origin}/mcp`;
  const cursor = `<div class="opt"><div class="oh"><span class="on">Cursor</span><span class="od">One-click install</span></div>
<a class="oneclick" href="${escapeHtml(cursorLink(mcpUrl))}">Add to Cursor &rarr;</a></div>`;
  const opts = commands(mcpUrl)
    .map(
      ([n, note, cmd], i) =>
        `<div class="opt"><div class="oh"><span class="on">${n}</span><span class="od">${note}</span></div>
<pre id="c${i}"><code>${escapeHtml(cmd)}</code></pre>
<button class="copy" type="button" data-for="c${i}" hidden>Copy</button></div>${i === 1 ? cursor : ""}`,
    )
    .join("");
  const inner = `<main id="main" class="narrow">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <h1 class="h1">Connect your <em>assistant</em></h1>
      <p class="lede">This briefing publishes its archive as a Model Context Protocol server, so your own assistant can read it directly &mdash; every issue, the running story threads, the sources with their bias ratings, and the cost of each run.</p>
      <p class="body">Answers come from the archive itself, not from a search index built over it: ask for an issue by date and you get that issue. Read-only, public data, no key, no account.</p>
      <div class="opts">${opts}</div>
      <p class="body">No client? Every tool is also a plain URL &mdash; start at <a href="${origin}/mcp/tools.json">the tool catalogue</a>, or read <a href="${origin}/mcp">the endpoint's own listing</a>.</p>
      <div class="tools">
        <h2>What your assistant can call</h2>
        <dl>${tools.map((t) => `<dt>${escapeHtml(t.name)}</dt><dd>${escapeHtml(t.description)}</dd>`).join("")}</dl>
      </div>
    </main>`;
  return pageHead(ctx, name(ctx), "Connect this briefing's archive to Claude, ChatGPT, or any MCP client.", connectCss) + pageBody(ctx, chrome, inner, [script(copyJs)]);
}

// ── ask ──
// Chosen by asking them: each exercises a different tool path, and none names a story, so none dates.
const SUGGESTIONS = ["What did I miss this week?", "Which story has developed the most this month?", "Where did outlets disagree in the latest issue?", "What is the briefing still waiting to find out?"];
export interface AskView {
  model: string;
  provider: string;
  openrouter: boolean;
}
export function askPage(ctx: PageCtx, origin: string, view: AskView | undefined): string {
  const chrome = subChrome(ctx.cfg, "", "/ask", "Ask the archive a question; answers cite the issue they came from.");
  const head = pageHead(ctx, name(ctx), "Ask a question about the briefing's archive; answers cite the issue they came from.", askCss);
  const attrs = ` data-origin="${escapeHtml(origin)}"`;
  if (!view) {
    const inner = `<main id="main" class="narrow">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <h1 class="h1">Ask the <em>archive</em></h1>
      <div class="off">The question box is not switched on for this deployment. You can still
      point your own assistant at the archive &mdash; see <a href="/connect">connect your
      assistant</a>.</div>
    </main>`;
    return head + pageBody(ctx, chrome, inner, [], attrs);
  }
  const provider = view.openrouter
    ? ', routed through <a href="https://openrouter.ai/">OpenRouter</a> (US) only to hosts that do not train on your question. If the first model is rate-limited the next one answers, and the name above changes to match; if all fail it tells you'
    : view.provider
      ? ` served by ${escapeHtml(view.provider)}`
      : "";
  const inner = `<main id="main" class="narrow">
      <a class="brandmark" href="/">${brand(ctx)}</a>
      <h1 class="h1">Ask the <em>archive</em></h1>
      <p class="lede">Every answer here is read out of the briefing's own archive by the same
      read-only tools any assistant can call, and cites the issue it came from. It has no
      opinions of its own and no knowledge beyond what has been published.</p>

      <div class="thread" id="thread" aria-live="polite"></div>

      <div class="askbox">
        <label class="asklabel" for="askq">Ask a question</label>
        <form class="askform" id="askform" novalidate>
          <textarea class="askin" id="askq" rows="2" maxlength="2000"
            placeholder="What has the briefing said about&hellip;"></textarea>
          <button class="asksend" id="asksend" type="submit">Ask</button>
        </form>
        <div class="chips" id="chips" aria-label="Example questions">${SUGGESTIONS.map((s) => `<button class="chip" type="button">${s}</button>`).join("")}</div>
      </div>

      <p class="fine">Answers are generated by <code id="askmodel">${escapeHtml(view.model)}</code>${provider}, from the archive's
      own search, issues, threads, sources and statistics. It can still be wrong, and the
      briefing it reads was itself written by a model &mdash; follow the issue links for what
      was actually published. Nothing you type is stored. Prefer your own assistant? See
      <a href="/connect">connect your assistant</a>.</p>
    </main>`;
  return head + pageBody(ctx, chrome, inner, [script(askJs)], attrs);
}

export const TAGLINE_ALL = TAG_ALL;
