// Internal identifiers must never reach reader text (runs 247, 2026-06-30). One definition.
// A delimited group of article ids: "[A221]", "(A316)", "(A110, A263)", "(A316, A317 and A318)".
// Bare "A316" is deliberately not matched: undelimited it cannot be told from "the A19 chip".
const ID_RUN = String.raw`A\d+(?:\s*(?:,|;|&|and)\s*A\d+)*`;
export const ARTICLE_ID_GROUP = new RegExp(String.raw`\s*(?:\[\s*${ID_RUN}\s*\]|\(\s*${ID_RUN}\s*\))`, "g");
const CLUSTER_REF = /\bclusters?\s+\d/i;

export const leaksInternalId = (text: string): boolean => CLUSTER_REF.test(text) || new RegExp(ARTICLE_ID_GROUP.source).test(text);

// eval_graders._leaked_ids, plus the cluster references above: every internal id visible in one
// reader-facing string, quoted. A bare id counts only when the story itself cites it, which is what
// tells "A238" from "the A19 chip". An id inside a delimited group is that group's leak, not another.
export function leakedIds(text: string, cited: readonly string[]): string[] {
  const spans: [number, number][] = [];
  const found: string[] = [];
  for (const m of text.matchAll(ARTICLE_ID_GROUP)) {
    spans.push([m.index, m.index + m[0].length]);
    found.push(m[0].trim());
  }
  for (const id of new Set(cited.filter((c) => /^A\d+$/.test(c))))
    for (const m of text.matchAll(new RegExp(String.raw`\b${id}\b`, "g"))) if (!spans.some(([a, b]) => a <= m.index && m.index < b)) found.push(id);
  for (const m of text.matchAll(new RegExp(CLUSTER_REF.source, "gi"))) found.push(m[0]);
  return found;
}

export function stripArticleIds(text: string): string {
  const out = text.replace(ARTICLE_ID_GROUP, "");
  return out === text ? text : out.replace(/\s{2,}/g, " ").trim();
}
