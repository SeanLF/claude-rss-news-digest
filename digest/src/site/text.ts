// Formatting shared by the pages, the feed and the MCP tools (circulation's util.rs).

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function escapeHtml(s: string): string {
  return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#x27;");
}

// YYYY-MM-DD, each part in range. Lenient on leading zeros ("2026-1-24"), as the Rust server was.
const inRange = (p: string, max: number): boolean => /^\d{1,3}$/.test(p) && Number(p) >= 1 && Number(p) <= max;
export function isValidDate(s: string): boolean {
  const segs = s.split("-");
  if (segs.length !== 3) return false;
  const [y, m, d] = segs as [string, string, string];
  return /^\d{4}$/.test(y) && inRange(m, 12) && inRange(d, 31);
}

function parts(date: string): [number, number, number] | undefined {
  const p = date.split("-");
  if (p.length !== 3) return undefined;
  const [y, m, d] = p.map(Number) as [number, number, number];
  if (!Number.isInteger(m) || m < 1 || m > 12 || !Number.isInteger(d)) return undefined;
  return [Number.isInteger(y) ? y : 2026, m, d];
}

// "2026-01-24" -> "Saturday, January 24"; anything else unchanged.
export function formatDate(date: string): string {
  const p = parts(date);
  if (!p) return date;
  const [y, m, d] = p;
  return `${DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]}, ${MONTHS[m - 1]} ${d}`;
}

// "2026-03" -> "March 2026"
export function formatMonthYear(ym: string): string {
  const [y, m] = ym.split("-");
  const n = Number(m);
  return y !== undefined && Number.isInteger(n) && n >= 1 && n <= 12 ? `${MONTHS[n - 1]} ${y}` : ym;
}

// "2026-02-06" -> "6 Feb 2026", the masthead dateline.
export function formatDayMonthYear(date: string): string {
  const p = parts(date);
  return p ? `${p[2]} ${MONTHS[p[1] - 1]!.slice(0, 3)} ${date.split("-")[0]}` : date;
}

// "2026-07-03" -> "3 Jul"
export function formatDayMonth(date: string): string {
  const p = parts(date);
  return p ? `${p[2]} ${MONTHS[p[1] - 1]!.slice(0, 3)}` : date;
}

// 3140 -> "3,140"
export const thousands = (n: number): string => n.toLocaleString("en-US");

// "News Digest" -> "News <em>Digest</em>": the last word carries the accent.
export function brandHtml(name: string): string {
  const t = name.trim();
  const i = t.search(/\s\S*$/);
  return i < 0 ? `<em>${escapeHtml(t)}</em>` : `${escapeHtml(t.slice(0, i))} <em>${escapeHtml(t.slice(i + 1))}</em>`;
}
