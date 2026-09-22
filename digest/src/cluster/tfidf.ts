// sklearn TfidfVectorizer() defaults, reproduced: lowercase, token pattern `\b\w\w+\b`, raw term
// counts, idf = ln((1 + n) / (1 + df)) + 1 (smooth_idf), l2-normalised rows. Cosine similarity of two
// rows is then their dot product. The join's fidelity test holds this equal to the archived output.
const TOKENS = /[\p{L}\p{N}_]{2,}/gu;

export type SparseVec = Map<string, number>;

export function tokenize(doc: string): string[] {
  return doc.toLowerCase().match(TOKENS) ?? [];
}

export function tfidf(docs: string[]): SparseVec[] {
  const counts = docs.map((d) => {
    const c = new Map<string, number>();
    for (const t of tokenize(d)) c.set(t, (c.get(t) ?? 0) + 1);
    return c;
  });
  const df = new Map<string, number>();
  for (const c of counts) for (const t of c.keys()) df.set(t, (df.get(t) ?? 0) + 1);
  const n = docs.length;
  return counts.map((c) => {
    const v: SparseVec = new Map();
    let norm = 0;
    for (const [t, tf] of c) {
      const w = tf * (Math.log((1 + n) / (1 + (df.get(t) ?? 0))) + 1);
      v.set(t, w);
      norm += w * w;
    }
    norm = Math.sqrt(norm);
    if (norm > 0) for (const [t, w] of v) v.set(t, w / norm);
    return v;
  });
}

export function cosine(a: SparseVec, b: SparseVec): number {
  const [small, large] = a.size <= b.size ? [a, b] : [b, a];
  let dot = 0;
  for (const [t, w] of small) dot += w * (large.get(t) ?? 0);
  return dot;
}
