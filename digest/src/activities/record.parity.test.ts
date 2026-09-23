import { existsSync, readFileSync } from "node:fs";
import { defaultTreeAdapter as t, parse, type DefaultTreeAdapterMap } from "parse5";
import { describe, expect, it } from "vitest";
import { webArchiveHtml } from "../render/web-archive.js";

// Host-only, like the render parity: the web copy the TypeScript stores, held node for node against
// the one db.prepare_for_web (bs4) made. RENDER_ORACLE holds bin/render-oracle's renders plus
// web.archive.html, db.prepare_for_web applied to each web.html. The rows the tail records are the
// product schema's (record.test.ts), and the import holds every archived issue byte for byte.
const REPO = new URL("../../../", import.meta.url).pathname;
const ORACLE = process.env["RENDER_ORACLE"] ?? `${REPO}data/replay/oracle`;
const RUNS = [300, 301, 302, 303, 304];

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
const present = existsSync(`${ORACLE}/inbox-markup/web.archive.html`) && RUNS.every((r) => existsSync(`${ORACLE}/run${r}-prod/web.archive.html`));
// A skipped host-only suite says so by name, so a green run without the oracle is not mistaken for one with it.
describe.runIf(!present)("record parity with the Python (host-only)", () => {
  it.skip(`SKIPPED: needs bin/record-oracle's output at ${ORACLE}; run bin/record-oracle 300 301 302 303 304`, () => undefined);
});
describe.skipIf(!present)("the web copy strips what db.prepare_for_web strips, on a page that carries it", () => {
  const page = present ? readFileSync(`${ORACLE}/inbox-markup/web.html`, "utf8") : "";
  const python = present ? readFileSync(`${ORACLE}/inbox-markup/web.archive.html`, "utf8") : "";
  it("the fixture carries each kind of inbox-only markup, and the Python's copy has none of it", () => {
    for (const marker of ['class="preheader"', 'email-only"', "[if mso"]) expect(page).toContain(marker);
    for (const marker of ["Russia votes;", "View in browser", "Unsubscribe", "(via Google News)", "[if mso"]) expect(python).not.toContain(marker);
    expect(python).toContain("email-only-ish");
  });
  it("node for node the Python's", () => {
    expect(canonical(webArchiveHtml(page))).toEqual(canonical(python));
  });
  it("and not the page as it came", () => {
    expect(canonical(page)).not.toEqual(canonical(python));
  });
});
describe.skipIf(!present)("the web copy of each archived run's page is the Python's, node for node", () => {
  it.each(RUNS)("run %i", (id) => {
    const page = readFileSync(`${ORACLE}/run${id}-prod/web.html`, "utf8");
    const python = readFileSync(`${ORACLE}/run${id}-prod/web.archive.html`, "utf8");
    expect(canonical(webArchiveHtml(page))).toEqual(canonical(python));
  });
});
