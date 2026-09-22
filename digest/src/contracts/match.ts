// How a coherence result is matched to a draft story (merge._result_matches): by the cited
// article_ids set, falling back to the normalised headline for a result without ids. Validation and
// assembly share this, so "covered" means the same thing in both.
export function normHeadline(headline: string): string {
  let t = (headline ?? "").normalize("NFKC");
  for (const [f, p] of [["’", "'"], ["‘", "'"], ["“", '"'], ["”", '"'], ["—", "-"], ["–", "-"]] as const) t = t.replaceAll(f, p);
  // str.casefold() differs from toLowerCase() on ß and final sigma; those two are folded explicitly.
  return t.replace(/\s+/g, " ").trim().replace(/[.,;:!?]+$/, "").toLowerCase().replaceAll("ß", "ss").replaceAll("ς", "σ");
}

export const itemIds = (sources: { article_id: string }[]): Set<string> => new Set(sources.map((s) => s.article_id));

export function resultMatches(result: { article_ids?: string[]; headline?: string }, ids: Set<string>, norm: string): boolean {
  const rids = result.article_ids ?? [];
  const rset = new Set(rids); // a set, as Python's frozenset: duplicate ids in a result still match
  if (rset.size && ids.size && rset.size === ids.size && [...rset].every((r) => ids.has(r))) return true;
  return result.headline !== undefined && normHeadline(result.headline) === norm;
}
