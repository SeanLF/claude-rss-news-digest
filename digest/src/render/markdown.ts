import { BUCKET_ORDER, BUCKET_WORD, bucketCounts, collectOutlets, isSafeUrl, outletLabel, type Outlet, type RenderInput, type Story } from "./common.js";

// The issue as Markdown, for agents: the body the site serves under its title line (`.md`, Accept:
// text/markdown). Written from the selections the web issue is rendered from, never from its HTML, and
// held to the document the site's HTML converter made of that page (markdown.test.ts).

// Text as Markdown reads it literally: \ * _ ` [ ] anywhere, a "<" that would open a tag, and a line
// start that would open a block (heading, quote, list item, ordered item, setext rule).
function text(s: string): string {
  let out = s.replaceAll(/[\\*_`[\]]/g, (c) => `\\${c}`).replaceAll(/<(?=[!?/A-Za-z])/g, "\\<");
  out = out.replace(/^(\s*)([#>=~]|[-+](?=\s)|\d+(?=[.)]\s))/, (_m, sp: string, lead: string) => (/^\d/.test(lead) ? `${sp}${lead}\\` : `${sp}\\${lead}`));
  return out.trim();
}
const cell = (s: string) => text(s).replaceAll("|", "\\|").replaceAll(/\s+/g, " ");
const href = (url: string) => url.replaceAll(/[()]/g, (c) => `\\${c}`).replaceAll(" ", "%20");
const link = (label: string, url: string) => `[${label}](${href(url)})`;

function sources(outlets: Outlet[]): string[] {
  if (!outlets.length) return [];
  const counts = bucketCounts(outlets);
  const present = BUCKET_ORDER.filter((b) => counts[b]);
  const spread = `${outlets.length} ${outlets.length === 1 ? "source" : "sources"} · ${present.map((b) => `${counts[b]} ${BUCKET_WORD[b]}`).join(" · ")}`;
  const rows = BUCKET_ORDER.flatMap((b) => outlets.filter((o) => o.bucket === b)).map((o) => {
    const { name, via, leaning } = outletLabel(o);
    return `| ${cell(via ? `${name} · via ${via}` : name)} | ${cell(leaning)} | ${o.urls.map((u, i) => link(String(i + 1), u)).join(" ")} |`;
  });
  return [spread, ["| Outlet | Leaning | Articles |", "| --- | --- | --- |", ...rows].join("\n")];
}

function story(a: Story, brief: boolean): string[] {
  const out = [`### ${text(a.headline ?? "")}`];
  const thread = a.thread ?? {};
  if ((thread.day ?? 0) >= 2) out.push(thread.url ? link(`Ongoing · day ${thread.day} ↗`, thread.url) : `Ongoing · day ${thread.day}`);
  // A continuing thread's delta, today's verified facts, replaces the summary.
  const body = (thread.delta ?? "").trim() || (a.summary ?? "");
  if (body.trim()) out.push(text(body));
  if (!brief) {
    const why = a.why_it_matters ?? "";
    if (why.trim()) out.push("**Why it matters**", text(why));
    const varies = a.reporting_varies ?? [];
    if (varies.length) out.push("**How reporting varies**", ...varies.map((rv) => `**${text(`${rv.source ?? ""}:`)}** ${text(rv.angle ?? "")}`));
  }
  out.push(...sources(collectOutlets(a)));
  return out;
}

export function renderMarkdown({ selections, env }: RenderInput): string {
  const assessors = env.archiveUrl && isSafeUrl(env.archiveUrl) ? link("independent media assessors", `${env.archiveUrl}/sources`) : "independent media assessors";
  const blocks = [
    `**AI-written** Written by Claude, an assistant that can make mistakes - verify anything important against the linked sources. Political leanings from ${assessors}.`,
    "## Must Know",
    ...selections.must_know.flatMap((a) => story(a, false)),
    "## Should Know",
    ...selections.should_know.flatMap((a) => story(a, true)),
  ];
  return blocks.join("\n\n");
}
