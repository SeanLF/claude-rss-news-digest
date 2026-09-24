import type { IssueRowData, SiteData } from "./data.js";
import { type Bucket, type CatalogueEntry, knownBucket } from "./sources.js";
import { escapeHtml, formatDayMonth, formatMonthYear } from "./text.js";

// The archive's running order (circulation's archive.rs): one page of issues with their bias split,
// rendered as the <li> rows the index and the /archive load-more fragment share.

export const DEFAULT_LIMIT = 30;
export const MAX_LIMIT = 100;

export interface IssueRow extends Omit<IssueRowData, "sourceIds"> {
  sourceCount: number;
  biasL: number;
  biasC: number;
  biasR: number;
}
export interface ArchivePage {
  issues: IssueRow[];
  hasMore: boolean;
  // The oldest date on this page, when another page follows.
  nextBefore: string | null;
}

// A parked source is kept: an issue really was built from it, and dropping its id would restate that
// issue's bias bar (circulation measured 150 issues moved, median 2 points).
export function biasMap(cat: CatalogueEntry[]): Map<string, Bucket> {
  const m = new Map<string, Bucket>();
  for (const s of cat) {
    const b = knownBucket(s.bias);
    if (b) m.set(s.id, b);
  }
  return m;
}

export function withBias(row: IssueRowData, bias: Map<string, Bucket>): IssueRow {
  const n = { l: 0, c: 0, r: 0 };
  for (const id of new Set(row.sourceIds)) {
    const b = bias.get(id);
    if (b) n[b]++;
  }
  const { sourceIds: _ids, ...rest } = row;
  return { ...rest, sourceCount: n.l + n.c + n.r, biasL: n.l, biasC: n.c, biasR: n.r };
}

// `year` wins over `before`; a year is unpaginated.
export async function fetchArchive(data: SiteData, bias: Map<string, Bucket>, q: { before?: string | undefined; year?: number | undefined; limit: number }): Promise<ArchivePage> {
  const limit = Math.min(Math.max(q.limit, 1), MAX_LIMIT);
  if (q.year !== undefined) {
    const rows = await data.archive({ year: q.year, limit });
    return { issues: rows.map((r) => withBias(r, bias)), hasMore: false, nextBefore: null };
  }
  const rows = await data.archive({ before: q.before, limit: limit + 1 });
  const hasMore = rows.length > limit;
  const issues = rows.slice(0, limit).map((r) => withBias(r, bias));
  return { issues, hasMore, nextBefore: hasMore ? (issues.at(-1)?.date ?? null) : null };
}

// Half-away-from-zero, as Rust's f64::round.
const round = (x: number): number => Math.sign(x) * Math.round(Math.abs(x));

// One issue <li>. The bias bar's aria-label carries the split as text (WCAG 1.4.1).
export function rowHtml(row: IssueRow, isToday: boolean): string {
  const total = row.biasL + row.biasC + row.biasR;
  let bias = "";
  if (total > 0) {
    const lp = round((row.biasL / total) * 100);
    const cp = round((row.biasC / total) * 100);
    const rp = 100 - lp - cp;
    bias = `<span class="bias" role="img" aria-label="${row.sourceCount} sources: ${row.biasL} left, ${row.biasC} center, ${row.biasR} right"><i class="l" style="width:${lp}%"></i><i class="c" style="width:${cp}%"></i><i class="r" style="width:${rp}%"></i></span>`;
  }
  return (
    `<li class="issue${isToday ? " today" : ""}" data-year="${row.date.slice(0, 4)}" data-date="${row.date}"><a href="/issues/${row.date}">` +
    `<span class="idx"><span class="no">${row.issueNo}</span><span class="date">${formatDayMonth(row.date)}</span></span>` +
    `<span class="main"><span class="sumline">${escapeHtml(row.preheader)}</span></span>` +
    `<span class="rt">${bias}<span class="count">${row.sourceCount} sources</span></span></a></li>`
  );
}

// Rows with a "Month YYYY" divider before each month's newest issue. Appended pages pass no `today`.
export function rowsHtml(rows: IssueRow[], today: string | null): string {
  return rows.map((r) => `${r.isMonthStart ? `<li class="month">${escapeHtml(formatMonthYear(r.date.slice(0, 7)))}</li>` : ""}${rowHtml(r, today === r.date)}`).join("");
}

// The /archive fragment: rows, then a hidden sentinel carrying the next cursor when more remain.
export function fragmentHtml(page: ArchivePage): string {
  const sentinel = page.nextBefore ? `<li class="more-sentinel" data-next-before="${page.nextBefore}" hidden aria-hidden="true"></li>` : "";
  return rowsHtml(page.issues, null) + sentinel;
}
