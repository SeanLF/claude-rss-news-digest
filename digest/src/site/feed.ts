import { escapeHtml, formatDate } from "./text.js";

// The Atom 1.0 feed of the newest issues (circulation's feed.rs), byte for byte: feed readers already
// hold its entry ids.

export const FEED_ENTRY_LIMIT = 30;

// `rows` newest first; `base` is "https://<domain>" or "" (links then stay root-relative).
export function atomFeed(feedName: string, base: string, rows: { date: string; preheader: string }[]): string {
  const title = escapeHtml(feedName);
  // Atom requires a feed-level <updated>: the newest issue's date, or the epoch with no issues yet.
  const updated = rows[0] ? `${rows[0].date}T00:00:00Z` : "1970-01-01T00:00:00Z";
  const entries = rows
    .map((r) => {
      const date = escapeHtml(r.date);
      const link = `${base}/issues/${date}`;
      const summary = r.preheader ? `\n    <summary>${escapeHtml(r.preheader)}</summary>` : "";
      return `
  <entry>
    <title>${escapeHtml(`${feedName} – ${formatDate(r.date)}`)}</title>
    <id>${link}</id>
    <link rel="alternate" type="text/html" href="${link}"/>
    <updated>${date}T00:00:00Z</updated>${summary}
  </entry>`;
    })
    .join("");
  return `<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>${title}</title>
  <id>${base}/</id>
  <updated>${updated}</updated>
  <author>
    <name>${title}</name>
  </author>
  <link rel="self" type="application/atom+xml" href="${base}/feed.xml"/>
  <link rel="alternate" type="text/html" href="${base}/"/>${entries}
</feed>
`;
}
