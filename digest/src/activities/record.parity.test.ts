import { constants, copyFileSync, existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { defaultTreeAdapter as t, parse, type DefaultTreeAdapterMap } from "parse5";
import { describe, expect, it } from "vitest";
import { attachThreads, issueNumber, loadAssets, renderWeb, resolveArticleIds, type RenderEnv, type Selections, type ThreadContext } from "../render/render.js";
import { ArtifactStore } from "../store/artifacts.js";
import { webArchiveHtml } from "../render/web-archive.js";
import { recordActivities } from "./record.js";
import { runActivities } from "./run.js";

// Host-only, like the render parity: the tail recorded by the TypeScript for archived runs, into a
// COPY of a production snapshot, held row by row against what the Python recorded for the same runs.
// RECORD_ORACLE_DB is the snapshot (opened read-only, never written); RENDER_ORACLE holds
// bin/render-oracle's renders (thread contexts, the render's one input the archive does not carry)
// plus web.archive.html, db.prepare_for_web applied to each web.html by bs4.
const REPO = new URL("../../../", import.meta.url).pathname;
const SNAPSHOT = process.env["RECORD_ORACLE_DB"] ?? `${REPO}data/prod-20260923.db`;
const ORACLE = process.env["RENDER_ORACLE"] ?? `${REPO}data/replay/oracle`;
const RUNS = [300, 301, 302, 303, 304];
const ENV: RenderEnv = { digestName: "Sean's Daily News Digest", digestDomain: "news-digest.seanfloyd.dev", archiveUrl: "https://news-digest.seanfloyd.dev", authorName: "Sean", authorUrl: "https://seanfloyd.dev" };

type Node = DefaultTreeAdapterMap["node"];
// One line per node of the parsed document: attributes sorted (bs4 writes them sorted, the spec
// serialiser as written), runs of HTML whitespace collapsed and whitespace-only text dropped (bs4
// drops indentation). What this cannot erase is a difference a browser would render.
function canonical(html: string): string[] {
  const out: string[] = [];
  const walk = (n: Node, depth: number): void => {
    if (t.isTextNode(n)) {
      const text = t.getTextNodeContent(n).replaceAll(/[ \t\n\r\f]+/g, " ").trim();
      if (text) out.push(`${depth}|#text ${text}`);
      return;
    }
    if (t.isCommentNode(n)) return void out.push(`${depth}|<!--${t.getCommentNodeContent(n)}-->`);
    if (t.isElementNode(n)) {
      const attrs = t.getAttrList(n).map((a) => `${a.name}=${JSON.stringify(a.value)}`).toSorted();
      out.push(`${depth}|<${t.getTagName(n)} ${attrs.join(" ")}>`);
    }
    if ("childNodes" in n) for (const c of n.childNodes) walk(c, depth + 1);
    if ("content" in n) for (const c of n.content.childNodes) walk(c, depth + 1);
  };
  walk(parse(html), 0);
  return out;
}
const unlinked = (line: string) => line.replace(/href="[^"]*"/, "href=?");
const undated = (line: string) => line.replaceAll(/day \d+/g, "day N");
const q = (d: DatabaseSync, sql: string, id: number) => d.prepare(sql).all(id);
// Differences the record does not own, counted rather than failed: a Google-News link production
// decoded at send (a network step no archive replays), an "Ongoing · day N" count that replay reads
// from today's thread state rather than the day's, and the stylesheet, which the port minifies with
// lightningcss (held equal by the render parity). Anything else is reported.
function htmlDiff(ours: string, theirs: string): { gnews: number; threadDay: number; style: number; other: string[] } {
  const a = canonical(ours);
  const b = canonical(theirs);
  const out = { gnews: 0, threadDay: 0, style: 0, other: [] as string[] };
  if (a.length !== b.length) return { ...out, other: [`${a.length} nodes vs ${b.length}`] };
  for (let i = 0; i < a.length; i++) {
    const x = a[i]!;
    const y = b[i]!;
    if (x === y) continue;
    if (x.includes('href="https://news.google.com/') && unlinked(x) === unlinked(y)) out.gnews++;
    else if (undated(x) === undated(y)) out.threadDay++;
    else if (x.includes("|#text :root{") && y.includes("|#text :root{")) out.style++;
    else out.other.push(`node ${i}:\n  ours:   ${x.slice(0, 300)}\n  theirs: ${y.slice(0, 300)}`);
  }
  return out;
}

const present = existsSync(SNAPSHOT) && RUNS.every((r) => existsSync(`${ORACLE}/run${r}-prod/web.archive.html`));
describe.skipIf(!present)("the recorded tail matches the Python's rows for runs 300-304", () => {
  const original = present ? new DatabaseSync(SNAPSHOT, { readOnly: true }) : undefined;
  const copy = join(mkdtempSync(join(tmpdir(), "record-oracle-")), "digest.db");
  if (present) copyFileSync(SNAPSHOT, copy, constants.COPYFILE_FICLONE);
  const db = new DatabaseSync(copy);
  const store = new ArtifactStore(copy);
  const record = recordActivities({ store, dbPath: copy });
  const run = runActivities({ store, dbPath: copy, sourcesFile: "/dev/null" });
  const assets = loadAssets({ templates: `${REPO}newsroom/templates`, design: `${REPO}design` });
  const expected = new Map<number, Record<string, unknown[]>>();
  const recorded = new Map<number, { html: string }>();

  it.each(RUNS)("run %i: records its tail into the copy", async (id) => {
    const o = original!;
    expected.set(id, {
      selections: q(o, "SELECT run_id, selections_json FROM selections WHERE run_id=? ORDER BY id", id),
      cluster_runs: q(o, "SELECT run_id, clusters_json FROM cluster_runs WHERE run_id=? ORDER BY id", id),
      shown_narratives: q(o, "SELECT headline, tier, source_id, original_title, cluster_id, run_id FROM shown_narratives WHERE run_id=? ORDER BY id", id),
      digests: q(o, "SELECT date, run_id, preheader, html, broadcast_id, broadcast_status, broadcast_recipients FROM digests WHERE run_id=?", id),
      digest_runs: q(o, "SELECT articles_kept, articles_emailed, status FROM digest_runs WHERE id=?", id),
    });
    // Wipe what the Python wrote for the run, keeping the send's own columns on the digests row
    // (the TypeScript's upsert must leave them as the send left them).
    db.prepare("DELETE FROM selections WHERE run_id=?").run(id);
    db.prepare("DELETE FROM cluster_runs WHERE run_id=?").run(id);
    db.prepare("DELETE FROM shown_narratives WHERE run_id=?").run(id);
    db.prepare("UPDATE digests SET html='', preheader='', run_id=NULL WHERE run_id=?").run(id);
    db.prepare("UPDATE digest_runs SET articles_kept=NULL, articles_emailed=NULL, status='running', completed_at=NULL WHERE id=?").run(id);

    const selections = store.find(id, "selections.json")!;
    const index = JSON.parse(store.get(store.find(id, "article_index.json")!)) as Record<string, unknown>;
    const threads = JSON.parse(readFileSync(`${ORACLE}/run${id}-prod/thread_context.json`, "utf8")) as Record<string, ThreadContext>;
    const at = (o.prepare("SELECT strftime('%Y-%m-%dT%H:%M:%SZ', completed_at) AS at FROM digest_runs WHERE id=?").get(id) as { at: string }).at;
    const web = renderWeb({ selections: attachThreads(resolveArticleIds(JSON.parse(store.get(selections)) as Selections, index), threads), now: new Date(at), issueNo: issueNumber(o, at.slice(0, 10)), env: ENV, assets });
    const html = store.put(id, "digest.html", web);
    await record.archiveRun(id, selections, store.find(id, "clusters.json")!);
    await record.saveDigest(id, html, selections);
    await record.recordShownHeadlines(id, selections);
    await run.finishRun(id, { stories: 0, broadcast: "sent", recipients: (expected.get(id)!["digest_runs"]![0] as { articles_emailed: number }).articles_emailed });
    recorded.set(id, { html: web });
  });

  describe.each(RUNS)("run %i", (id) => {
    const got = (sql: string) => q(db, sql, id);
    it("selections: the archived blob, byte for byte", () => {
      expect(got("SELECT run_id, selections_json FROM selections WHERE run_id=? ORDER BY id")).toEqual(expected.get(id)!["selections"]);
    });
    it("cluster_runs: the archived blob, byte for byte", () => {
      expect(got("SELECT run_id, clusters_json FROM cluster_runs WHERE run_id=? ORDER BY id")).toEqual(expected.get(id)!["cluster_runs"]);
    });
    it("shown_narratives: every row, in order", () => {
      const rows = got("SELECT headline, tier, source_id, original_title, cluster_id, run_id FROM shown_narratives WHERE run_id=? ORDER BY id");
      expect(rows.length).toBeGreaterThan(0);
      expect(rows).toEqual(expected.get(id)!["shown_narratives"]);
    });
    it("digest_runs: kept, emailed and status", () => {
      expect(got("SELECT articles_kept, articles_emailed, status FROM digest_runs WHERE id=?")).toEqual(expected.get(id)!["digest_runs"]);
    });
    it("digests: the row's columns, and the page up to decoded Google-News links", () => {
      const [ours] = got("SELECT date, run_id, preheader, html, broadcast_id, broadcast_status, broadcast_recipients FROM digests WHERE run_id=?") as Record<string, unknown>[];
      const [theirs] = expected.get(id)!["digests"] as Record<string, unknown>[];
      expect({ ...ours, html: undefined }).toEqual({ ...theirs, html: undefined });
      const diff = htmlDiff(ours!["html"] as string, theirs!["html"] as string);
      expect(diff.other).toEqual([]);
      console.log(JSON.stringify({ run: id, ...diff, other: diff.other.length }));
    });
    // The strip itself, on one input: the web copy the TypeScript stores against the one
    // db.prepare_for_web (bs4 4.15.0, the locked version) makes of the Python oracle's own page.
    it("digests: the web copy of a page is the Python's web copy of it, node for node", () => {
      const page = readFileSync(`${ORACLE}/run${id}-prod/web.html`, "utf8");
      const python = readFileSync(`${ORACLE}/run${id}-prod/web.archive.html`, "utf8");
      expect(canonical(webArchiveHtml(page))).toEqual(canonical(python));
    });
    // Negative control: the comparison must see a page one story short.
    it("digests: a page missing one story does not compare equal", () => {
      const [theirs] = expected.get(id)!["digests"] as Record<string, unknown>[];
      const short = recorded.get(id)!.html.replace(/<article class="brief"[\s\S]*?<\/article>/, "");
      expect(short).not.toBe(recorded.get(id)!.html);
      expect(htmlDiff(short, theirs!["html"] as string).other).not.toEqual([]);
    });
  });
});
