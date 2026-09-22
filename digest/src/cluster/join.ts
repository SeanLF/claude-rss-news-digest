import { agnes } from "ml-hclust";
import { tagBag, TOKEN_RE, type Tag } from "./tags.js";
import { cosine, tfidf } from "./tfidf.js";

export interface Cluster {
  story: string;
  article_ids: string[];
}

// Threshold 0.80 is the held-out value from runs 204/205 (docs/2026-07-01-graph-gate-preregistration.md).
export const JOIN_THRESHOLD = 0.8;
// A cluster this small that shares a modal label with a larger one is a fragment of it (run 235).
export const STRAY_ABSORB_MAX = 2;

// Deterministically group articles by their extracted tags: TF-IDF over the tag bag, agglomerative
// clustering with average linkage on cosine distance, cut at the threshold. Every input id lands in
// exactly one cluster, the invariant SELECT relies on; `story` is the cluster's modal primary_event.
export function joinTags(articleIds: string[], tags: Record<string, Tag>, threshold = JOIN_THRESHOLD): Cluster[] {
  if (articleIds.length === 0) return [];
  if (articleIds.length === 1) {
    const aid = articleIds[0]!;
    return [{ story: tags[aid]?.primary_event || "cluster 1", article_ids: [aid] }];
  }
  // A tagless article gets a unique token so it stays a singleton (never merges on emptiness).
  const docs = articleIds.map((aid, i) => {
    const bag = tagBag(tags[aid]);
    return TOKEN_RE.test(bag) ? bag : `notags${i}`;
  });
  const vecs = tfidf(docs);
  const n = vecs.length;
  const dist: number[][] = Array.from({ length: n }, () => Array.from({ length: n }, () => 0));
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      const d = Math.max(0, 1 - cosine(vecs[i]!, vecs[j]!));
      dist[i]![j] = d;
      dist[j]![i] = d;
    }
  const tree = agnes(dist, { method: "average", isDistanceMatrix: true });
  // sklearn's labels follow the merge order; we order clusters by their first article for a stable output.
  const groups = tree.cut(threshold).map((c) => c.indices().toSorted((a, b) => a - b)).toSorted((a, b) => a[0]! - b[0]!);
  const clusters: Cluster[] = groups.map((members, k) => {
    const ids = members.map((i) => articleIds[i]!);
    return { story: modalEvent(ids, tags) || `cluster ${k + 1}`, article_ids: ids };
  });
  return mergeSameStory(clusters);
}

// The most common non-empty primary_event; a tie goes to the first seen, as Counter.most_common does.
function modalEvent(ids: string[], tags: Record<string, Tag>): string {
  const counts = new Map<string, number>();
  for (const a of ids) {
    const pe = tags[a]?.primary_event;
    if (pe) counts.set(pe, (counts.get(pe) ?? 0) + 1);
  }
  let best = "";
  let bestN = 0;
  for (const [pe, c] of counts)
    if (c > bestN) {
      best = pe;
      bestN = c;
    }
  return best;
}

// Fold stray clusters into a same-`story` anchor so the label stays a usable identity key: the
// largest cluster with a duplicated label is the anchor (ties to the first); every sibling with at
// most absorbMax articles folds into it; a substantial sibling is left separate (force-merging two
// real stories is worse than the collision, which the render layer guards against).
export function mergeSameStory(clusters: Cluster[], absorbMax = STRAY_ABSORB_MAX): Cluster[] {
  const positions = new Map<string, number[]>();
  clusters.forEach((c, i) => positions.set(c.story, [...(positions.get(c.story) ?? []), i]));
  const anchorOf = new Map<string, number>();
  for (const [story, idxs] of positions)
    if (idxs.length > 1) anchorOf.set(story, idxs.reduce((best, i) => (clusters[i]!.article_ids.length > clusters[best]!.article_ids.length ? i : best)));
  if (anchorOf.size === 0) return clusters;
  const out: Cluster[] = [];
  clusters.forEach((c, i) => {
    const anchor = anchorOf.get(c.story);
    if (anchor === undefined) out.push(c);
    else if (anchor === i) {
      const ids = [...c.article_ids];
      const seen = new Set(ids);
      for (const j of positions.get(c.story) ?? [])
        if (j !== i && clusters[j]!.article_ids.length <= absorbMax)
          for (const a of clusters[j]!.article_ids)
            if (!seen.has(a)) {
              seen.add(a);
              ids.push(a);
            }
      out.push({ story: c.story, article_ids: ids });
    } else if (c.article_ids.length > absorbMax) out.push(c);
    // else: folded into its anchor
  });
  return out;
}
