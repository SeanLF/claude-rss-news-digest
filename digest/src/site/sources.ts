import { readFileSync } from "node:fs";

// The source catalogue (newsroom/sources.json) as the site reads it. Parked sources ("active": false)
// stay in it: past issues were built from them, so the history-facing figures keep them, and only
// the surfaces about today's shelf leave them out.

export interface CatalogueEntry {
  id: string;
  name: string;
  url: string;
  bias: string;
  factuality: string;
  perspective: string;
  region: string;
  active: boolean;
}

export function loadCatalogue(file: string): CatalogueEntry[] {
  const raw = JSON.parse(readFileSync(file, "utf8")) as unknown;
  if (!Array.isArray(raw)) throw new Error(`${file} is not a list`);
  return raw.map((s: Record<string, unknown>, i) => {
    const str = (k: string, required: boolean): string => {
      const v = s[k];
      if (typeof v === "string") return v;
      if (required) throw new Error(`${file}[${i}] missing ${k}`);
      return "";
    };
    return {
      id: str("id", true),
      name: str("name", true),
      url: str("url", true),
      bias: str("bias", true),
      factuality: str("factuality", true),
      perspective: str("perspective", false),
      region: str("region", false),
      active: s["active"] !== false,
    };
  });
}

export type Bucket = "l" | "c" | "r";

// The archive's bias bar: only a known lean counts.
export function knownBucket(bias: string): Bucket | undefined {
  if (["far-left", "left", "lean-left"].includes(bias)) return "l";
  if (bias === "center") return "c";
  if (["lean-right", "right", "far-right"].includes(bias)) return "r";
  return undefined;
}
// The sources page and stats fold anything unrecognised into the centre.
export const bucket = (bias: string): Bucket => (["far-left", "left", "lean-left"].includes(bias) ? "l" : ["lean-right", "right", "far-right"].includes(bias) ? "r" : "c");

export const parkedIds = (cat: CatalogueEntry[]): Set<string> => new Set(cat.filter((s) => !s.active).map((s) => s.id));

// The outlet's homepage, from the feed URL the pipeline polls.
export function websiteFromRss(rssUrl: string, name: string): string {
  const special: Record<string, string> = {
    "BBC World": "https://www.bbc.com",
    "Hacker News": "https://news.ycombinator.com",
    "Nikkei Asia": "https://asia.nikkei.com",
    "Wall Street Journal": "https://www.wsj.com",
  };
  if (special[name]) return special[name];
  if (rssUrl.startsWith("https://news.google.com")) {
    // The site: operator sits in a query string and ends at the first character a hostname cannot
    // hold: `q=site:reuters.com+when:1d` once shipped readers to https://reuters.com+when:1d.
    const m = /site:([A-Za-z0-9.-]+)/.exec(rssUrl);
    if (m?.[1]) return `https://${m[1]}`;
  }
  if (rssUrl.startsWith("https://")) {
    const host = rssUrl.slice("https://".length).split("/")[0]!;
    return `https://${host.replace(/^(feeds|rss)\./, "")}`;
  }
  return rssUrl;
}

export interface SourceRow {
  name: string;
  website: string;
  bias: string;
  factuality: string;
  perspective: string;
  feedCount: number;
}

const GROUPED: [string, string][] = [
  ["economist_", "The Economist"],
  ["scmp_", "South China Morning Post"],
  ["haaretz_", "Haaretz"],
];

const titleCase = (w: string): string => (w ? w[0]!.toUpperCase() + w.slice(1) : "");

// Today's shelf for /sources: active entries, multi-feed outlets folded into one row, by name.
export function sourceRows(cat: CatalogueEntry[]): SourceRow[] {
  const seen = new Map<string, { s: CatalogueEntry; n: number }>();
  for (const s of cat.filter((c) => c.active)) {
    const key = GROUPED.find(([p]) => s.id.startsWith(p))?.[1] ?? s.name;
    const got = seen.get(key);
    if (got) got.n++;
    else seen.set(key, { s, n: 1 });
  }
  return [...seen.values()]
    .map(({ s, n }) => ({
      name: GROUPED.find(([p]) => s.id.startsWith(p))?.[1] ?? s.name,
      website: websiteFromRss(s.url, s.name),
      bias: s.bias,
      factuality: s.factuality,
      perspective: s.perspective.split("_").map(titleCase).join(" "),
      feedCount: n,
    }))
    .toSorted((a, b) => (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0));
}

// get_sources: the active catalogue as Markdown, one bullet per feed.
export function sourcesMarkdown(cat: CatalogueEntry[]): string {
  let out = "# Sources\n\nBias and factuality are Media Bias/Fact Check ratings. Bias runs far-left, left, lean-left, center, lean-right, right, far-right.\n\n";
  for (const s of cat.filter((c) => c.active)) {
    out += `- **${s.name}** — bias: ${s.bias} · factuality: ${s.factuality}`;
    if (s.region) out += ` · region: ${s.region}`;
    if (s.perspective) out += ` · perspective: ${s.perspective.replaceAll("_", " ")}`;
    out += ` · ${s.url}\n`;
  }
  return out;
}
