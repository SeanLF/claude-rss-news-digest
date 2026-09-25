import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { sameDocument, rendered } from "../site/parity/document.js";
import { issueMarkdownBody } from "../store/html-markdown.js";
import { loadAssets, renderMarkdown, renderWeb, type RenderInput, type Selections } from "./render.js";

const REPO = new URL("../../../", import.meta.url).pathname;
const assets = loadAssets({ templates: `${REPO}digest/templates`, design: `${REPO}design` });
const edge = () => JSON.parse(readFileSync(new URL("./fixtures/edge_selections.json", import.meta.url), "utf8")) as Selections;
const kitchenSink = () => JSON.parse(readFileSync(`${REPO}digest/src/render/fixtures/kitchensink_selections.json`, "utf8")) as Selections;
// The web page fills a {{DATE}} that editorial text quotes (the Python's order of fills, kept for
// parity); the Markdown keeps it literal. That one difference aside, they are the same document.
const edgeNoPlaceholder = (): Selections => JSON.parse(JSON.stringify(edge()).replaceAll("{{DATE}}", "a date")) as Selections;
const env = { digestName: "Digest", digestDomain: "news.example", archiveUrl: "https://news.example", authorName: "Sean", authorUrl: "https://author.example/" };
const input = (selections: Selections, over: Partial<RenderInput["env"]> = {}): RenderInput => ({ selections, now: new Date("2026-09-18T10:42:40Z"), issueNo: 277, env: { ...env, ...over }, assets });

describe("the issue as Markdown, from the selections", () => {
  // The contract: the document agents got when the site converted the rendered page at request time.
  it.each([
    ["edge", input(edgeNoPlaceholder())],
    ["kitchen sink", input(kitchenSink())],
    ["no archive URL", input(edgeNoPlaceholder(), { archiveUrl: "" })],
    ["no should-know", input({ ...kitchenSink(), should_know: [] })],
  ])("is the document the converter made of the web page: %s", (_n, i) => {
    const fromHtml = issueMarkdownBody(renderWeb(i), "2026-09-18");
    const ours = renderMarkdown(i);
    expect(fromHtml).toBeDefined();
    // Story by story, so a failure names the one that differs.
    if (!sameDocument(fromHtml!, ours)) expect(rendered(ours).split("<h3>")).toEqual(rendered(fromHtml!).split("<h3>"));
    expect(sameDocument(fromHtml!, ours)).toBe(true);
  });

  it("keeps a placeholder that editorial text quotes as it was written", () => {
    expect(renderMarkdown(input(edge()))).toContain("it quotes {{DATE}} literally");
  });

  it("escapes what Markdown would read as markup in editorial text", () => {
    const sel: Selections = {
      must_know: [{ headline: "*Not* [a link] <b>", summary: "# not a heading", sources: [{ name: "A | B", url: "https://a.example/x_(1)", bias: "center" }] }],
      should_know: [],
    };
    const out = rendered(renderMarkdown(input(sel)));
    expect(out).toContain("<h3>*Not* [a link] &lt;b&gt;</h3>");
    expect(out).toContain("<p># not a heading</p>");
    expect(out).toContain("<td>A | B</td>");
    expect(out).toContain('<a href="https://a.example/x_(1)">1</a>');
  });
});
