import { readFileSync } from "node:fs";
import { htmlEscape } from "escape-goat";
import { describe, expect, it } from "vitest";
import { roundHalfEven, slugify } from "./common.js";
import { applyDecodedLinks, attachThreads, collapseReposts, loadAssets, renderEmail, renderWeb, resolveArticleIds, type RenderInput, type Selections } from "./render.js";

const REPO = new URL("../../../", import.meta.url).pathname;
const assets = loadAssets({ templates: `${REPO}newsroom/templates`, design: `${REPO}design` });
const edge = () => JSON.parse(readFileSync(new URL("./fixtures/edge_selections.json", import.meta.url), "utf8")) as Selections;
const env = { digestName: "Digest", digestDomain: "news.example", archiveUrl: "https://news.example", authorName: "Sean", authorUrl: "https://author.example/" };
const input = (selections: Selections): RenderInput => ({ selections, now: new Date("2026-09-18T10:42:40Z"), issueNo: 277, env, assets });

describe("the Python's arithmetic and text rules", () => {
  it("rounds halves to even, as Python's round() does", () => {
    expect([0.5, 1.5, 2.5, 12.5, 62.5, 2.4, 2.6].map(roundHalfEven)).toEqual([0, 2, 2, 12, 62, 2, 3]);
  });
  it("slugs to ASCII, cut at 60 without a trailing hyphen, 'story' when nothing is left", () => {
    expect(slugify("Zürich café — 文A")).toBe("z-rich-caf-a");
    expect(slugify("!!!")).toBe("story");
    expect(slugify(`${"a".repeat(59)} b`)).toBe("a".repeat(59));
  });
});

describe("resolution", () => {
  const index = {
    A1: { name: "Reuters", url: "https://www.reuters.com/a", bias: "center", source_id: "reuters", original_title: "Deal signed - Reuters", wire: true },
    A2: { name: "Straits Times", url: "https://st.example/a", bias: "center", source_id: "st", original_title: "Deal signed" },
    A3: { name: "BBC", url: "https://bbc.example/a", bias: "center", source_id: "bbc", original_title: "Another take" },
    A4: { name: "Broken", url: "https://x.example/a" },
  };
  it("resolves ids, collapses a verbatim repost onto the wire origin, and drops what it cannot resolve", () => {
    const sel: Selections = { must_know: [{ headline: "h", sources: [{ article_id: "A2" }, { article_id: "A1" }, { article_id: "A3" }, { article_id: "A9" }] }], should_know: [{ headline: "gone", sources: [{ article_id: "A4" }] }] };
    const out = resolveArticleIds(sel, index);
    expect(out.must_know[0]!.sources.map((s) => s.name)).toEqual(["Reuters", "BBC"]);
    expect(out.should_know).toEqual([]);
  });
  it("keeps untitled sources in place, never merged", () => {
    expect(collapseReposts([{ name: "a" }, { name: "b" }]).length).toBe(2);
  });
  it("upgrades a decoded Google-News link and leaves the rest", () => {
    const sel: Selections = { must_know: [{ sources: [{ name: "Reuters", url: "https://news.google.com/rss/articles/X" }, { name: "BBC", url: "https://bbc.example/a" }] }], should_know: [] };
    expect(applyDecodedLinks(sel, { "https://news.google.com/rss/articles/X": "https://www.reuters.com/x" }).must_know[0]!.sources.map((s) => s.url)).toEqual(["https://www.reuters.com/x", "https://bbc.example/a"]);
  });
  it("attaches thread context by cluster, and to neither of two stories sharing one", () => {
    const sel: Selections = { must_know: [{ cluster_id: "a", sources: [] }, { cluster_id: "b", sources: [] }, { cluster_id: "b", sources: [] }], should_know: [] };
    const out = attachThreads(sel, { a: { day: 3, url: "/thread/1" }, b: { day: 4 } });
    expect(out.must_know.map((s) => s.thread?.day)).toEqual([3, undefined, undefined]);
  });
});

describe("the web issue", () => {
  it("fills placeholders literally: editorial text carrying $& and $1 survives", () => {
    const html = renderWeb(input(edge()));
    expect(html).toContain("Because $1 &amp; $&amp; and $&#39; matter");
    expect(html).toContain('<a href="https://author.example/privacy">Privacy</a>');
    expect(html).not.toMatch(/\{\{[A-Z_]+\}\}/);
  });
  it("refuses to ship an unfilled placeholder", () => {
    expect(() => renderWeb({ ...input(edge()), assets: { ...assets, template: `${assets.template}{{UNKNOWN_SLOT}}` } })).toThrow(/Unfilled placeholder in rendered digest: \{\{UNKNOWN_SLOT\}\}/);
  });
});

// The seam the spec names (§6): two renderers, one data source. Every story the web issue shows,
// the email shows, in the same order, with the same headline, body and source count.
const text = (s: string) => s.replaceAll(/<[^>]+>/g, " ").replaceAll("&nbsp;", " ").replaceAll(/\s+/g, " ");
const sourceCounts = (t: string) => [...t.matchAll(/(\d+) (?:source|sources) · /gi)].map((m) => m[1]);
describe("email and web show the same issue", () => {
  it.each([["edge", edge()], ["kitchen sink", JSON.parse(readFileSync(`${REPO}newsroom/tests/fixtures/kitchensink_selections.json`, "utf8")) as Selections]] as const)("%s", (_n, sel) => {
    const web = text(renderWeb(input(sel)));
    const email = text(renderEmail(input(sel)));
    let wAt = 0;
    let eAt = 0;
    for (const s of [...sel.must_know, ...sel.should_know]) {
      const body = (s.thread?.delta ?? "").trim() || (s.summary ?? "");
      for (const field of [s.headline ?? "", body].filter((f) => !/\{\{[A-Z_]+\}\}/.test(f))) {
        const want = text(htmlEscape(field));
        const w = web.indexOf(want, wAt);
        const e = email.indexOf(want, eAt);
        expect(w, `web lacks ${field}`).toBeGreaterThanOrEqual(0);
        expect(e, `email lacks ${field}`).toBeGreaterThanOrEqual(0);
        wAt = w + 1;
        eAt = e + 1;
      }
    }
    expect(sourceCounts(email)).toEqual(sourceCounts(web));
  });
  // Inherited from the Python, kept for parity and reported: the web fills placeholders across the
  // whole page, editorial text included, so a story quoting {{DATE}} reads differently in each.
  it("diverges where editorial text quotes a placeholder", () => {
    const sel = edge();
    expect(text(renderWeb(input(sel)))).toContain("it quotes Friday, September 18, 2026 literally");
    expect(text(renderEmail(input(sel)))).toContain("it quotes {{DATE}} literally");
  });
});

describe("the web stylesheet", () => {
  // lightningcss with no targets upgrades syntax: `(min-width:760px)` became `(width>=760px)`, which
  // Safari before 16.4 reads as false, dropping both layouts. The Python ships the old syntax.
  const css = /<style>([\s\S]*?)<\/style>/.exec(renderWeb(input(edge())))?.[1] ?? "";
  it("keeps media queries in the syntax older Safari reads", () => {
    expect(css).toMatch(/\(min-width:\s*760px\)/);
    expect(css).not.toMatch(/\(width\s*[<>]=?/);
  });
  it("keeps the underline thickness as its own property", () => {
    expect(css).toMatch(/text-decoration-thickness:/);
  });
});
