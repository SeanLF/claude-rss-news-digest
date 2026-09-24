import TurndownService from "turndown";

// Stored issue HTML to Markdown (circulation's markdown.rs, on htmd): what the site served at request
// time until the pipeline wrote Markdown itself. It now runs once, in cli/backfill-markdown, to fill
// issues.markdown for the issues published before that.

// The editorial <main> of a stored issue, or the whole document for older blobs without one.
function extractMain(html: string): string {
  const start = html.indexOf("<main");
  if (start < 0) return html;
  const end = html.indexOf("</main>", start);
  if (end < 0) {
    console.warn(JSON.stringify({ site: "markdown", warning: "<main> without </main>; the issue may be truncated" }));
    return html;
  }
  return html.slice(start, end + "</main>".length);
}

const hasClass = (el: HTMLElement, c: string): boolean => (el.getAttribute("class") ?? "").split(/\s+/).includes(c);

// htmd's escaping, so the Markdown matches what the Rust server served: a leading `=`, `~` or `>`, a
// list marker, an ATX heading or an ordered-list number is escaped, and so are \ * _ ` [ ] anywhere;
// then "<" that would open a tag.
function escapeText(text: string): string {
  const first = text[0];
  if (first === undefined) return text;
  let out = text;
  if (/[=~>\-+#0-9]/.test(first) || /[\\*_`[\]]/.test(text)) {
    const lead = "=~>".includes(first) || ("-+".includes(first) && text[1] === " ") || (first === "#" && /^#+ /.test(text));
    out = (lead ? "\\" : "") + text.replaceAll(/[\\*_`[\]]/g, (c) => `\\${c}`);
    if (/[0-9]/.test(first)) out = out.replace(/^(\d+)\.(?= )/, "$1\\.");
  }
  return out.replaceAll(/<(?=[!?]|\/[A-Za-z]|[A-Za-z])/g, (m, i: number) => (out.startsWith("<![CDATA[", i) ? m : "\\<"));
}

// Characters as Rust counts them: code points, not UTF-16 units.
const charLen = (s: string): number => Array.from(s).length;
const normCell = (s: string): string => s.replaceAll("\n", " ").replaceAll("\r", "").replaceAll("|", "&#124;").replace(/^[\t\n\r ]+|[\t\n\r ]+$/g, "");

// htmd's table: a header row, a dashed rule, cells padded to each column's widest raw cell, pipes in a
// cell written as &#124;.
function tableMarkdown(table: HTMLElement, cell: (el: HTMLElement) => string): string {
  // Array.from: domino's collections are array-like, not iterable.
  const rowsOf = (sel: string): HTMLElement[] => Array.from(table.querySelectorAll<HTMLElement>(sel));
  const cells = (tr: HTMLElement, tag: string) =>
    Array.from(tr.childNodes)
      .filter((c): c is HTMLElement => c.nodeName === tag)
      .map(cell);
  let headers: string[] = [];
  const rows: string[][] = [];
  const head = rowsOf("thead tr")[0];
  if (head) headers = cells(head, "TH").length ? cells(head, "TH") : cells(head, "TD");
  for (const tr of rowsOf("tbody tr")) {
    if (!head && !headers.length) {
      headers = cells(tr, "TH");
      if (headers.length) continue;
    }
    const r = cells(tr, "TD");
    if (r.length) rows.push(r);
  }
  const n = Math.max(headers.length, ...rows.map((r) => r.length));
  if (!n) return "";
  const widths = Array.from({ length: n }, (_, i) => Math.max(charLen(headers[i] ?? ""), ...rows.map((r) => charLen(r[i] ?? ""))));
  const line = (r: string[]) => `|${widths.map((w, i) => ` ${normCell(r[i] ?? "")}${" ".repeat(Math.max(w - charLen(normCell(r[i] ?? "")), 0))} |`).join("")}\n`;
  let md = "\n\n";
  if (headers.length) md += line(headers) + `|${widths.map((w) => ` ${"-".repeat(w)} |`).join("")}\n`;
  for (const r of rows) md += line(r);
  return `${md}\n`;
}

function converter(): TurndownService {
  const td = new TurndownService({ headingStyle: "atx", bulletListMarker: "*", codeBlockStyle: "fenced", emDelimiter: "*", strongDelimiter: "**" });
  td.escape = escapeText;
  td.remove(["script", "style"]);
  // An issue from before the template had a <main> is converted whole, head included, and htmd sets its
  // <title> apart as a block.
  td.addRule("title", { filter: "title", replacement: (content) => `\n\n${content}\n\n` });
  const cellTd = new TurndownService({ headingStyle: "atx", bulletListMarker: "*", emDelimiter: "*", strongDelimiter: "**" });
  cellTd.escape = escapeText;
  td.addRule("table", {
    filter: "table",
    replacement: (_content, node) => tableMarkdown(node, (el) => cellTd.turndown(el.innerHTML).replace(/^[\t\n\r ]+|[\t\n\r ]+$/g, "")),
  });
  // aria-hidden is the markup saying "decoration, do not read aloud"; a Markdown reader asks the same
  // question a screen reader does.
  td.addRule("decorative", { filter: (n) => n.nodeType === 1 && n.getAttribute("aria-hidden") === "true", replacement: () => "" });
  // Styled labels are bolded, never dropped: `tag` also marked bias labels in the 2025-12 issues.
  td.addRule("label", {
    filter: (n) => (n.nodeName === "SPAN" || n.nodeName === "DIV") && (hasClass(n, "lbl") || hasClass(n, "tag")),
    replacement: (content) => {
      const t = content.trim();
      return t && !t.includes("\n") ? `**${t}** ` : content;
    },
  });
  // An anchor with no words (the copy-link anchor, an icon) is not something a reader can read.
  td.addRule("empty-link", { filter: (n) => n.nodeName === "A" && !(n.textContent ?? "").trim(), replacement: () => "" });
  return td;
}
let td: TurndownService | undefined;

// An issue's Markdown body, the document under the site's title line, or undefined when its HTML
// yields none (the backfill then leaves the issue without Markdown rather than store an empty one).
export function issueMarkdownBody(html: string, date: string): string | undefined {
  let body: string;
  try {
    // htmd trims the spaces a line ends on before a block starts (a bolded label before its paragraph).
    body = (td ??= converter()).turndown(extractMain(html)).replaceAll(/ +(?=\n\n)/g, "").trim();
  } catch (e) {
    console.error(JSON.stringify({ markdown: "backfill", date, error: String(e) }));
    return undefined;
  }
  if (!body) {
    console.error(JSON.stringify({ markdown: "backfill", date, error: "the issue's HTML yields no Markdown body" }));
    return undefined;
  }
  return body;
}
