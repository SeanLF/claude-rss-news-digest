// usage: backfill-markdown
// Fills issues.markdown, once, for the issues published before the pipeline wrote Markdown itself:
// each row still NULL is converted from its stored HTML by the converter the site ran per request
// until then, so agents get the Markdown they got before. Rows already filled are left alone, so a
// rerun is a no-op. Exits 1 naming each revision whose HTML yielded no Markdown (the site 404s those
// as Markdown and still serves the page).
import { dbUrl, openDb, type Sql } from "../store/db.js";
import { issueMarkdownBody } from "../store/html-markdown.js";

export async function backfillMarkdown(db: Sql): Promise<{ filled: number; failed: string[] }> {
  const rows = await db.all<{ date: string; revision: number; html: string }>("SELECT issue_date::text AS date, revision, html FROM issues WHERE markdown IS NULL ORDER BY issue_date, revision");
  let filled = 0;
  const failed: string[] = [];
  for (const r of rows) {
    const body = issueMarkdownBody(r.html, r.date);
    if (body === undefined) failed.push(`${r.date} r${r.revision}`);
    else filled += await db.run("UPDATE issues SET markdown = $1 WHERE issue_date = $2::date AND revision = $3 AND markdown IS NULL", [body, r.date, r.revision]);
  }
  return { filled, failed };
}

if (process.argv[1]?.endsWith("backfill-markdown.js")) {
  const { filled, failed } = await backfillMarkdown(openDb(dbUrl()));
  console.log(`backfill-markdown: ${filled} issue revisions filled${failed.length ? `; no Markdown from ${failed.join(", ")}` : ""}`);
  process.exit(failed.length ? 1 : 0); // the pool would otherwise hold the process open
}
