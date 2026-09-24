import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { siteStore } from "../site/store.js";
import { testApp, testConfig } from "../site/testing.js";
import { openDb } from "../store/db.js";
import { migratedDb } from "../store/test-db.js";
import { backfillMarkdown } from "./backfill-markdown.js";

// Two stored issues from the dev database, one from before the template had a <main> and one after,
// each with the Markdown the site served for it on 2026-09-24, when it still converted at request time.
const fixture = (name: string) => readFileSync(new URL(`./fixtures/backfill-markdown/${name}`, import.meta.url), "utf8");
const DATES = ["2025-12-05", "2026-09-14"];

async function seeded() {
  const db = openDb(await migratedDb());
  for (const d of DATES) await db.run("INSERT INTO issues (issue_date, revision, html, preheader) VALUES ($1, 1, $2, '')", [d, fixture(`${d}.html`)]);
  return db;
}

describe("backfill-markdown", () => {
  it("fills every issue so the site serves the Markdown it converted at request time, byte for byte", async () => {
    const db = await seeded();
    expect(await backfillMarkdown(db)).toEqual({ filled: 2, failed: [] });
    const app = testApp(siteStore(db), { cfg: testConfig({ DIGEST_NAME: "Sean's Daily Digest" }) });
    for (const d of DATES) {
      const res = await app.request(`/issues/${d}.md`);
      expect(res.status, d).toBe(200);
      expect(await res.text(), d).toBe(fixture(`${d}.md`));
    }
  });

  it("fills every revision, and leaves Markdown already written (by the pipeline, or an earlier backfill) as it is", async () => {
    const db = await seeded();
    await db.run("INSERT INTO issues (issue_date, revision, html, preheader, markdown) VALUES ('2026-09-14', 2, '<main><p>new</p></main>', '', 'from the pipeline')");
    expect(await backfillMarkdown(db)).toEqual({ filled: 2, failed: [] });
    expect(await backfillMarkdown(db)).toEqual({ filled: 0, failed: [] });
    expect(await db.all("SELECT issue_date, revision, markdown IS NOT NULL AS filled FROM issues ORDER BY 1, 2")).toEqual([
      { issue_date: "2025-12-05", revision: 1, filled: true },
      { issue_date: "2026-09-14", revision: 1, filled: true },
      { issue_date: "2026-09-14", revision: 2, filled: true },
    ]);
    expect((await db.one<{ markdown: string }>("SELECT markdown FROM issues WHERE revision = 2"))?.markdown).toBe("from the pipeline");
  });

  it("leaves an issue whose HTML yields no Markdown empty, and names it", async () => {
    const db = openDb(await migratedDb());
    await db.run("INSERT INTO issues (issue_date, revision, html) VALUES ('2026-01-01', 1, '<main></main>')");
    expect(await backfillMarkdown(db)).toEqual({ filled: 0, failed: ["2026-01-01 r1"] });
    expect(await db.one("SELECT markdown FROM issues")).toEqual({ markdown: null });
  });
});
