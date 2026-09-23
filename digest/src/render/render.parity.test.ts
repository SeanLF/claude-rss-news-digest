import { existsSync, readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { transform } from "lightningcss";
import { describe, expect, it } from "vitest";
import { attachThreads, issueNumber, loadAssets, renderEmail, renderWeb, resolveArticleIds, type RenderEnv, type RenderInput, type Selections, type Story, type ThreadContext } from "./render.js";

// Host-only: the production DB clone and the Python oracle's renders are not in the CI image.
// bin/render-oracle RUN... writes the oracle: the Python render of each archived run (at its
// completed_at) and of each fixture, under a production-like and an empty environment.
const REPO = new URL("../../../", import.meta.url).pathname;
const DB = process.env["DIGEST_DB"] ?? `${REPO}data/digest.db`;
const ORACLE = process.env["RENDER_ORACLE"] ?? `${REPO}data/replay/oracle`;
const FIXTURE_AT = new Date("2026-07-05T06:07:00Z");
const FIXTURES: Record<string, string> = {
  kitchensink: `${REPO}newsroom/tests/fixtures/kitchensink_selections.json`,
  edge: new URL("./fixtures/edge_selections.json", import.meta.url).pathname,
};
const ENVS: Record<string, RenderEnv> = {
  prod: { digestName: "Sean's Daily News Digest", digestDomain: "news-digest.seanfloyd.dev", archiveUrl: "https://news-digest.seanfloyd.dev", authorName: "Sean", authorUrl: "https://seanfloyd.dev" },
  bare: { digestName: undefined, digestDomain: "", archiveUrl: "", authorName: "", authorUrl: "" },
};
const assets = loadAssets({ templates: `${REPO}newsroom/templates`, design: `${REPO}design` });

// The two byte differences the port carries, each normalised away here and nowhere else:
// escape-goat writes an apostrophe as &#39; where Python's html.escape writes &#x27; (the same
// character reference), and lightningcss minifies the stylesheet differently from the Python's
// regex minifier. The stylesheets are held equal by re-minifying both with lightningcss; CSS keeps
// a custom property's value verbatim, so the whitespace after its commas is the one thing that
// re-minifying cannot settle.
const canonicalCss = (css: string) => transform({ code: Buffer.from(css), minify: true, filename: "digest.css" }).code.toString().replaceAll(/,\s+/g, ",");
const normalise = (html: string) => html.replaceAll("&#39;", "&#x27;").replace(/<style>([\s\S]*?)<\/style>/, (_m, css: string) => `<style>${canonicalCss(css)}</style>`);

// The first differing offset after normalising both sides, with context either side, or null when
// equal: a readable failure.
function firstDiff(oursRaw: string, oracleRaw: string): string | null {
  const ours = normalise(oursRaw);
  const oracle = normalise(oracleRaw);
  if (ours === oracle) return null;
  let i = 0;
  while (i < ours.length && ours[i] === oracle[i]) i++;
  return `at ${i}:\n  ours:   ${JSON.stringify(ours.slice(Math.max(0, i - 80), i + 80))}\n  oracle: ${JSON.stringify(oracle.slice(Math.max(0, i - 80), i + 80))}`;
}

function withFirstSources(input: RenderInput, pick: (s: Story["sources"]) => Story["sources"]): RenderInput {
  const [first, ...rest] = input.selections.must_know;
  return { ...input, selections: { ...input.selections, must_know: [{ ...first!, sources: pick(first!.sources) }, ...rest] } };
}

interface Case { name: string; env: RenderEnv; input: () => RenderInput; oracle: string }
function cases(db: DatabaseSync): Case[] {
  const out: Case[] = [];
  for (const dir of readdirSync(ORACLE).toSorted()) {
    const m = /^(run(\d+)|kitchensink|edge)-(prod|bare)$/.exec(dir);
    if (!m) continue;
    const env = ENVS[m[3]!]!;
    const oracle = `${ORACLE}/${dir}`;
    if (m[2]) {
      const runId = Number(m[2]);
      const art = (name: string) => (db.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(runId, name) as { content: string }).content;
      const { at } = db.prepare("SELECT strftime('%Y-%m-%dT%H:%M:%SZ', completed_at) AS at FROM digest_runs WHERE id=?").get(runId) as { at: string };
      const now = new Date(at);
      const threads = JSON.parse(readFileSync(`${oracle}/thread_context.json`, "utf8")) as Record<string, ThreadContext>;
      out.push({ name: dir, env, oracle, input: () => {
        const resolved = resolveArticleIds(JSON.parse(art("selections.json")) as Selections, JSON.parse(art("article_index.json")) as Record<string, Record<string, unknown>>);
        return { selections: attachThreads(resolved, threads), now, issueNo: issueNumber(db, at.slice(0, 10)), env, assets };
      } });
    } else {
      out.push({ name: dir, env, oracle, input: () => ({ selections: JSON.parse(readFileSync(FIXTURES[m[1]!]!, "utf8")) as Selections, now: FIXTURE_AT, issueNo: null, env, assets }) });
    }
  }
  return out;
}

// A skipped describe still collects, so the database is opened only when it is there.
const present = existsSync(DB) && existsSync(ORACLE);
describe.skipIf(!present)("render parity with the Python oracle", () => {
  const db = present ? new DatabaseSync(DB, { readOnly: true }) : undefined;
  const all = db ? cases(db) : [];
  it("has an oracle for every run from 300 on in the database, and for both fixtures", () => {
    const runs = (db!.prepare("SELECT id FROM digest_runs WHERE id >= 300 AND completed_at IS NOT NULL ORDER BY id").all() as { id: number }[]).map((r) => r.id);
    const names = all.map((c) => c.name);
    for (const env of ["prod", "bare"]) {
      for (const id of runs) expect(names).toContain(`run${id}-${env}`);
      expect(names).toContain(`kitchensink-${env}`);
      expect(names).toContain(`edge-${env}`);
    }
  });
  it.each(all.map((c) => [c.name, c] as const))("%s: the web issue matches the Python's bytes", (_n, c) => {
    expect(firstDiff(renderWeb(c.input()), readFileSync(`${c.oracle}/web.html`, "utf8"))).toBeNull();
  });
  it.each(all.map((c) => [c.name, c] as const))("%s: the email matches the Python's bytes", (_n, c) => {
    expect(firstDiff(renderEmail(c.input()), readFileSync(`${c.oracle}/email.html`, "utf8"))).toBeNull();
  });
  // The negative control: the comparison above must fail when the port loses one story or one link.
  // The web issue links every article; the email links none, only each story's "view sources
  // online", so its one link goes with the story's sources.
  it.each(all.filter((c) => c.name.endsWith("-prod")).map((c) => [c.name, c] as const))("%s: dropping one story or one link breaks parity", (_n, c) => {
    const web = readFileSync(`${c.oracle}/web.html`, "utf8");
    const email = readFileSync(`${c.oracle}/email.html`, "utf8");
    const input = c.input();
    const dropStory = { ...input, selections: { ...input.selections, should_know: input.selections.should_know.slice(0, -1) } };
    expect(firstDiff(renderWeb(dropStory), web)).not.toBeNull();
    expect(firstDiff(renderEmail(dropStory), email)).not.toBeNull();
    expect(firstDiff(renderWeb(withFirstSources(input, (s) => s.slice(1))), web)).not.toBeNull();
    expect(firstDiff(renderEmail(withFirstSources(input, () => [])), email)).not.toBeNull();
  });
});
