import { type ArchivePage, rowsHtml } from "../archive.js";
import { subscriptionsEnabled } from "../config.js";
import type { IndexMeta } from "../data.js";
import { escapeHtml, formatDayMonthYear, thousands } from "../text.js";
import { indexCss, indexJs } from "./blobs.js";
import { type PageCtx, brand, indexChrome, pageBody, pageHead, script } from "./chrome.js";

// The home page: the archive as an issue-numbered running order.

export const NOTICES: Record<string, string> = {
  subscribed: '<p class="notice ok" role="status"><span class="ni" aria-hidden="true">✓</span> Subscribed. The next issue will land in your inbox.</p>',
  pending:
    '<p class="notice ok" role="status"><span class="ni" aria-hidden="true">✓</span> Almost there — check your inbox (and your spam folder, just in case) and click the link to confirm your subscription.</p>',
  subscribe_invalid: '<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> That address looks invalid or disposable — please try a different email.</p>',
  subscribe_ratelimited: '<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> Too many attempts — please try again in a little while.</p>',
  subscribe_error: '<p class="notice bad" role="alert"><span class="ni" aria-hidden="true">✕</span> That didn\'t go through — something failed on our end. Please try again.</p>',
};

export type IndexScope = { kind: "all" } | { kind: "before"; before: string } | { kind: "year"; year: number };

const loadmore = (hasMore: boolean, next: string): string =>
  `<div class="loadmore" id="loadmore">${hasMore ? `<a class="btn secondary" id="loadMore" rel="next" href="/?before=${escapeHtml(next)}">Load older issues</a>` : ""}<p class="loadmore-status" role="status" aria-live="polite"></p></div>`;

const segButton = (seg: string, active: string, label: string): string => `<button type="button" data-seg="${seg}" aria-pressed="${seg === active}">${label}</button>`;

export function indexPage(ctx: PageCtx, meta: IndexMeta, scope: IndexScope, page: ArchivePage | undefined, notice: string, markdownPointer: string): string {
  const subs = subscriptionsEnabled(ctx.cfg);
  let list: string;
  let more = "";
  let segment = "all";
  const hasIssues = meta.total > 0 && page !== undefined;
  const ul = (rows: string) => `<ul class="index" id="index" data-total="${meta.total}">${rows}</ul>`;
  if (!hasIssues) {
    list = `<p class="empty">No issues published yet. The first digest lands after the next morning run${subs ? " — subscribe below and it'll be in your inbox." : "."}</p>`;
  } else if (scope.kind === "year") {
    list = ul(rowsHtml(page.issues, null));
    more = loadmore(false, "");
    segment = "year";
  } else if (scope.kind === "before") {
    list = ul(rowsHtml(page.issues, null));
    // The no-JS older page offers a way back to the newest issues.
    more = `${loadmore(page.hasMore, page.nextBefore ?? "")}<p style="text-align:center;margin-top:16px"><a href="/">↑ Back to the newest issues</a></p>`;
  } else {
    list = ul(rowsHtml(page.issues, meta.newestDate));
    more = loadmore(page.hasMore, page.nextBefore ?? "");
  }
  const toolbar = hasIssues
    ? `<div class="toolbar">
      <form class="search" role="search" action="/search" method="get">
        <input type="search" name="q" placeholder="Search past headlines&hellip;" aria-label="Search past headlines">
      </form>
    </div>
    <div class="browse">
      <div class="seg" role="group" aria-label="Filter by period">
        ${segButton("all", segment, "All")}${segButton("year", segment, "This year")}${segButton("recent", segment, "Recent")}
      </div>
      <label class="datejump"><span>Jump to</span><input type="date" id="dateJump" min="${meta.firstDate ?? ""}" max="${meta.newestDate ?? ""}" aria-label="Jump to a date"></label>
    </div>
    <div class="listhead"><span>Issue</span><span>In this issue</span><span class="r">Sources</span></div>`
    : "";
  const subband = subs
    ? `<section class="subband" id="subscribe">
      <div class="copy"><h2>Get it in your inbox</h2><p>One briefing each morning. Free, no tracking, unsubscribe anytime.</p></div>
      <form method="post" action="/subscribe" aria-label="Subscribe">
        <input type="email" name="email" placeholder="your@email.com" required aria-label="Email address">
        <button class="btn primary" type="submit">Subscribe</button>
      </form>
    </section>`
    : "";
  const stat = `<b>${meta.total}</b> issues &middot; since ${meta.firstDate ? formatDayMonthYear(meta.firstDate) : ""} &middot; <b>${thousands(meta.totalStories)}</b> stories`;
  const head = pageHead(ctx, escapeHtml(ctx.cfg.digestName), "Daily briefing on geopolitics, tech, and privacy. All sides. No fluff.", indexCss, `\n<link rel="alternate" type="text/markdown" href="/index.md">`);
  const inner = `<header class="masthead">
      <h1 class="brand">${brand(ctx)}</h1>
      <div class="sub"><span class="kicker">Geopolitics &middot; Tech &middot; Privacy &middot; All sides, no fluff</span><span class="stat">${stat}</span></div>
    </header>
    <main id="main">${markdownPointer}
    ${notice}
    ${toolbar}
    ${list}
    ${more}
    ${subband}
    </main>`;
  return head + pageBody(ctx, indexChrome(ctx.cfg), inner, [script(ctx, indexJs)]);
}
