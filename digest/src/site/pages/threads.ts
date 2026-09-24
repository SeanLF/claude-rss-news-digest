import type { ThreadDetail, ThreadEntry, ThreadIndexPage, ThreadSummary } from "../threads.js";
import { escapeHtml, formatDayMonthYear } from "../text.js";
import { threadCss, threadsCss, threadsJs } from "./blobs.js";
import { type PageCtx, brand, pageBody, pageHead, script, subChrome, subMasthead } from "./chrome.js";
import { TAGLINE_ALL } from "./sub.js";

// The threads index (a status-grouped list that pages like the archive) and a thread's page (status,
// the story so far, the "still watching" ledger and a dated timeline).

// Only "active" is live; everything else, dormant or unknown, is the hollow muted state.
const statusParts = (status: string): [string, string] => (status === "active" ? ["on", "Ongoing"] : ["dorm", "Dormant"]);
const tsDate = (ts: string): string => formatDayMonthYear(ts.slice(0, 10));

function threadRow(t: ThreadSummary): string {
  const [cls, label] = statusParts(t.status);
  const last = t.summary ? `<span class="last">${escapeHtml(t.summary)}</span>` : "";
  return `<li><a href="/thread/${t.id}"><span class="mk ${cls}" aria-hidden="true"></span><span class="tl"><span class="lbl">${escapeHtml(t.label)}</span>${last}</span><span class="meta"><span class="st ${cls}">${label}</span><span class="upd">${tsDate(t.updatedAt)} &middot; ${t.updateCount} update${t.updateCount === 1 ? "" : "s"}</span></span></a></li>`;
}

// `total` is the full count: "30 threads" above a paged list of 607 would be a lie.
function section(rows: ThreadSummary[], label: string, total: number, id: string): string {
  if (!rows.length) return "";
  return `<section><div class="sec-h"><h2>${label}</h2><span class="ct">${total} ${total === 1 ? "thread" : "threads"}</span></div><ul class="threads" id="${id}" data-total="${total}">${rows.map(threadRow).join("")}</ul></section>`;
}

// The cursor rides in the markup, so the fragment stays a list the browser can render unaided.
const sentinel = (next: ThreadIndexPage["nextBefore"]): string =>
  next ? `<li class="more-sentinel" hidden data-next-before="${escapeHtml(next.updatedAt)}" data-next-id="${next.id}"></li>` : "";

export const threadsFragment = (page: ThreadIndexPage): string => page.older.map(threadRow).join("") + sentinel(page.nextBefore);

export function threadsPage(ctx: PageCtx, page: ThreadIndexPage, deep: boolean): string {
  const chrome = subChrome(ctx.cfg, "threads", "/threads", "A thread groups a running story's daily updates. Ongoing threads carry today's digest forward.");
  const empty = !page.ongoing.length && !page.older.length;
  // A real <a href> so the list pages with JS off.
  const more = page.nextBefore
    ? `<div class="loadmore" id="loadmore"><a class="btn secondary" id="loadMore" rel="next" href="/threads?before=${escapeHtml(encodeURIComponent(page.nextBefore.updatedAt))}&amp;before_id=${page.nextBefore.id}">Load older threads</a><p class="loadmore-status" role="status" aria-live="polite"></p></div>`
    : "";
  const back = deep ? '<p style="text-align:center;margin-top:16px"><a href="/threads">&uarr; Back to the newest threads</a></p>' : "";
  const body = empty
    ? '<p class="empty">No threads yet — evolving stories appear here once the digest starts tracking them across days.</p>'
    : section(page.ongoing, "Ongoing", page.ongoing.length, "ongoing-threads") + section(page.older, "Earlier", page.olderTotal, "older-threads") + more + back;
  const inner = `${subMasthead(brand(ctx), "Threads", "Ongoing stories, tracked across days", `<b>${page.ongoing.length}</b> ongoing &middot; <b>${page.olderTotal}</b> earlier`)}
    <main id="main">
    ${body}
    </main>`;
  return pageHead(ctx, escapeHtml(ctx.cfg.digestName), "Ongoing stories the digest is tracking across days.", threadsCss) + pageBody(ctx, chrome, inner, [script(ctx, threadsJs)]);
}

function timelineEntry(e: ThreadEntry): string {
  const issue = e.issueDate ? `<a class="issue" href="/issues/${e.issueDate}">&rarr; in the ${formatDayMonthYear(e.issueDate)} issue</a>` : "";
  const when = `<div class="when"><span class="date">${formatDayMonthYear(e.day)}</span>${issue}</div>`;
  // An update with no renderable facts is a quiet, carried-forward day.
  if (!e.facts.length) {
    const head = e.headline ? escapeHtml(e.headline) : "No new developments — the thread carried forward without a fresh installment.";
    return `<li class="update quiet">${when}<h3 class="uhead">${head}</h3></li>`;
  }
  return `<li class="update">${when}<h3 class="uhead">${escapeHtml(e.headline)}</h3><ul class="facts">${e.facts.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul></li>`;
}

export function threadPage(ctx: PageCtx, id: number, d: ThreadDetail): string {
  const chrome = subChrome(ctx.cfg, "", `/thread/${id}`, TAGLINE_ALL);
  const [dot, statusLabel] = statusParts(d.status);
  const story = d.entries[0]?.facts.join(" ") || "This thread is being tracked; the first installment will appear here.";
  const since = d.entries.length ? formatDayMonthYear(d.entries.at(-1)!.day) : "";
  const n = d.entries.length;
  const meta = since ? `${n} update${n === 1 ? "" : "s"}<span class="divider" aria-hidden="true">·</span>since ${since}` : `${n} update${n === 1 ? "" : "s"}`;
  const ledger = d.openQuestions.length
    ? `<section class="ledger" aria-labelledby="watch"><h2 id="watch" class="ledger-label">Still watching</h2><ul class="qlist">${d.openQuestions.map((q) => `<li>${escapeHtml(q)}</li>`).join("")}</ul></section>`
    : "";
  const inner = `<header class="mast">
      <p class="brandline">${brand(ctx)}</p>
      <div class="statusrow">
        <span class="dot ${dot}" aria-hidden="true"></span>
        <span class="status${dot === "on" ? "" : " off"}">${statusLabel}</span>
        <span class="span">${meta}</span>
      </div>
      <h1>${escapeHtml(d.label)}</h1>
      <p class="sofar"><span class="lead">The story so far</span>${escapeHtml(story)}</p>
    </header>
    <main id="main">
      ${ledger}
      <section aria-labelledby="tl">
        <h2 id="tl" class="tl-label">How it developed</h2>
        <ol class="timeline">${d.entries.map(timelineEntry).join("")}</ol>
      </section>
      <p class="backlink"><a href="/threads">&larr; All threads</a></p>
    </main>`;
  return pageHead(ctx, escapeHtml(d.label), escapeHtml(story), threadCss) + pageBody(ctx, chrome, inner);
}
