// usage: check-injections
// Renders every stored issue's web page from the product database (DIGEST_DATABASE_URL) and exits 1,
// listing each date whose stored HTML is missing a needle the site injects its chrome at. The served
// page logs the same miss per request; this finds them all at once, before a reader does.
import { loadAssets } from "../site/assets.js";
import { siteConfig } from "../site/config.js";
import type { SiteData } from "../site/data.js";
import type { PageCtx } from "../site/pages/chrome.js";
import { renderIssue } from "../site/pages/issue.js";
import { siteStore } from "../site/store.js";
import { dbUrl, openDb } from "../store/db.js";

export interface Miss {
  date: string;
  needles: string[];
  error?: string;
}

// A contact address is forced on, so the feedback line's needles are checked on every issue.
export async function checkInjections(ctx: PageCtx, dates: string[], issue: SiteData["issue"]): Promise<Miss[]> {
  const checked: PageCtx = { ...ctx, cfg: { ...ctx.cfg, contactEmail: ctx.cfg.contactEmail ?? "check@invalid" } };
  const misses: Miss[] = [];
  for (const date of dates) {
    const stored = await issue(date);
    if (!stored) {
      misses.push({ date, needles: [], error: "no stored issue" });
      continue;
    }
    const { missed } = renderIssue(checked, date, stored, "");
    if (missed.length) misses.push({ date, needles: missed });
  }
  return misses;
}

if (process.argv[1]?.endsWith("check-injections.js")) {
  // Subscriptions play no part in an issue page, and the worker's environment configures them for mail.
  const cfg = siteConfig({ ...process.env, RESEND_API_KEY: "", RESEND_AUDIENCE_ID: "" });
  const db = openDb(dbUrl());
  const dates = (await db.all<{ date: string }>("SELECT DISTINCT issue_date::text AS date FROM issues ORDER BY 1")).map((r) => r.date);
  const store = siteStore(db);
  const misses = await checkInjections({ cfg, assets: loadAssets(cfg.designDir) }, dates, (d) => store.issue(d));
  for (const m of misses) console.log(JSON.stringify(m));
  console.log(`check-injections: ${dates.length} issues rendered, ${misses.length} with a missed injection`);
  process.exit(misses.length ? 1 : 0); // the pool would otherwise hold the process open
}
