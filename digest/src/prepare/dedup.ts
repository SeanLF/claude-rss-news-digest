import { STOPWORDS } from "./stopwords.js";

// newsroom/src/dedup.py, ported: TF-IDF over recently shown RSS titles, idf = ln(n / (1 + df)),
// tf normalised by the document's max count. Not sklearn's variant: this is the matcher whose 0.80
// threshold is deployed, so it is reproduced as it is.
export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_\s]/gu, " ")
    .split(/\s+/)
    .filter((w) => w && !STOPWORDS.has(w));
}

type Vec = Map<string, number>;

export class TfidfMatcher {
  private readonly idf = new Map<string, number>();
  private readonly vectors: Vec[];
  constructor(private readonly headlines: string[]) {
    const docs = headlines.map(tokenize);
    const df = new Map<string, number>();
    for (const d of docs) for (const w of new Set(d)) df.set(w, (df.get(w) ?? 0) + 1);
    for (const [w, n] of df) this.idf.set(w, Math.log(docs.length / (1 + n)));
    this.vectors = docs.map((d) => this.vector(d));
  }
  private vector(doc: string[]): Vec {
    const v: Vec = new Map();
    if (!doc.length) return v;
    const tf = new Map<string, number>();
    for (const w of doc) tf.set(w, (tf.get(w) ?? 0) + 1);
    const max = Math.max(...tf.values());
    for (const [w, c] of tf) {
      const idf = this.idf.get(w);
      if (idf !== undefined) v.set(w, (c / max) * idf);
    }
    return v;
  }
  private static cosine(a: Vec, b: Vec): number {
    if (!a.size || !b.size) return 0;
    let dot = 0;
    for (const [w, x] of a) {
      const y = b.get(w);
      if (y !== undefined) dot += x * y;
    }
    if (dot === 0) return 0;
    const mag = (v: Vec) => Math.sqrt([...v.values()].reduce((s, x) => s + x * x, 0));
    return dot / (mag(a) * mag(b));
  }
  findMostSimilar(text: string): { headline?: string; score: number } {
    const q = this.vector(tokenize(text));
    let best: { headline?: string; score: number } = { score: 0 };
    this.vectors.forEach((v, i) => {
      const s = TfidfMatcher.cosine(q, v);
      if (s > best.score) best = { headline: this.headlines[i]!, score: s };
    });
    return best;
  }
}
