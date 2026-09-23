import { describe, expect, it } from "vitest";
import { webArchiveHtml } from "./web-archive.js";

const page = (body: string) => `<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>T</title>\n</head>\n<body>\n${body}\n</body>\n</html>`;

describe("webArchiveHtml (db.prepare_for_web)", () => {
  it("drops email-only nodes, the preheader and MSO conditional comments", () => {
    const out = webArchiveHtml(page(`<span class="preheader">Inbox preview</span><p class="note email-only">View in browser</p><!--[if mso]><table><![endif]--><div class="paper"><p class="footer-meta">Kept</p><!-- a plain comment --></div>`));
    expect(out).not.toContain("Inbox preview");
    expect(out).not.toContain("View in browser");
    expect(out).not.toContain("[if mso");
    expect(out).toContain("<!-- a plain comment -->");
    expect(out).toContain('<p class="footer-meta">Kept</p>');
  });
  it("keeps the doctype and every needle the web tier injects at", () => {
    const out = webArchiveHtml(page(`<div class="paper"><footer><p class="footer-meta">x</p></footer></div>`));
    expect(out.startsWith("<!DOCTYPE html>")).toBe(true);
    for (const needle of ["</head>", "<body>", '<div class="paper">', '<p class="footer-meta">', "</footer>", "</body>"]) expect(out).toContain(needle);
  });
  it("leaves a page with nothing to strip as it was, escapes included", () => {
    const body = `<div class="paper"><h3 class="head">Sean's &amp; <b>bold</b></h3><a title="a &quot;b&quot; &amp;amp;" href="https://x.test/?a=1&amp;copy=2">1</a></div>`;
    expect(webArchiveHtml(page(body))).toBe(`<!DOCTYPE html><html lang="en"><head>\n<meta charset="utf-8">\n<title>T</title>\n</head>\n<body>\n${body}\n\n</body></html>`);
  });
});
