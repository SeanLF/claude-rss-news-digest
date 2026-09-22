// Internal identifiers must never reach reader text (runs 247, 2026-06-30). One definition.
// A delimited group of article ids: "[A221]", "(A316)", "(A110, A263)", "(A316, A317 and A318)".
// Bare "A316" is deliberately not matched: undelimited it cannot be told from "the A19 chip".
const ID_RUN = String.raw`A\d+(?:\s*(?:,|;|&|and)\s*A\d+)*`;
export const ARTICLE_ID_GROUP = new RegExp(String.raw`\s*(?:\[\s*${ID_RUN}\s*\]|\(\s*${ID_RUN}\s*\))`, "g");
const CLUSTER_REF = /\bclusters?\s+\d/i;

export const leaksInternalId = (text: string): boolean => CLUSTER_REF.test(text) || new RegExp(ARTICLE_ID_GROUP.source).test(text);

export function stripArticleIds(text: string): string {
  const out = text.replace(ARTICLE_ID_GROUP, "");
  return out === text ? text : out.replace(/\s{2,}/g, " ").trim();
}
