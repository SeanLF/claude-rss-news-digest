import { z } from "zod";
import type { Selections, Source, Story, ThreadContext } from "./common.js";

// One article_index.json entry, as prepare writes it; wire_agency predates some archived indexes.
const IndexEntry = z.object({ name: z.string(), url: z.string(), bias: z.string(), source_id: z.string(), original_title: z.string(), wire: z.boolean().optional(), wire_agency: z.string().nullable().optional() });

// digest._repost_key: a title normalised for verbatim-repost matching, less the " - <Source>"
// suffix Google-News-fetched feeds append, matched against the source's own name only.
function repostKey(title: string, sourceName: string): string {
  const suffix = ` - ${sourceName}`;
  let t = title;
  if (sourceName && t.toLowerCase().endsWith(suffix.toLowerCase())) t = t.slice(0, -suffix.length);
  return t.toLowerCase().replaceAll(/[^a-z0-9 ]/g, " ").replaceAll(/\s+/g, " ").trim();
}

// digest.collapse_reposts: within one story, reposts of the same item (same normalised title)
// collapse to one source, the wire origin when there is one, else the first listed.
export function collapseReposts(sources: Source[]): Source[] {
  const keyed = sources.map((s) => [repostKey(s.original_title ?? "", s.name ?? ""), s] as const);
  const groups = new Map<string, Source[]>();
  for (const [key, src] of keyed) if (key) groups.set(key, [...(groups.get(key) ?? []), src]);
  const out: Source[] = [];
  const emitted = new Set<string>();
  for (const [key, src] of keyed) {
    if (!key) {
      out.push(src);
      continue;
    }
    if (emitted.has(key)) continue;
    emitted.add(key);
    const group = groups.get(key)!;
    out.push(group.find((s) => s.wire) ?? group[0]!);
  }
  return out;
}

// digest.resolve_article_ids: each {article_id} becomes its outlet, link and leaning from the run's
// article index; an id the index lacks is dropped, and so is a story left with no source.
export function resolveArticleIds(selections: Selections, index: Record<string, unknown>): Selections {
  let unresolved = 0;
  const resolve = (src: Source): Source | null => {
    if (!src.article_id) return src;
    const meta = IndexEntry.safeParse(Object.hasOwn(index, src.article_id) ? index[src.article_id] : undefined);
    if (!meta.success) {
      unresolved++;
      return null;
    }
    const { name, url, bias, source_id, original_title, wire, wire_agency } = meta.data;
    return { name, url, bias, source_id, original_title, wire: wire ?? false, wire_agency };
  };
  const tier = (stories: Story[]) =>
    stories.flatMap((item) => {
      const sources = item.sources.map(resolve).filter((s): s is Source => s !== null);
      if (!sources.length) {
        console.warn(JSON.stringify({ stage: "render", warning: "dropped a story with no resolved sources", headline: item.headline }));
        return [];
      }
      return [{ ...item, sources: collapseReposts(sources) }];
    });
  const out = { ...selections, must_know: tier(selections.must_know), should_know: tier(selections.should_know) };
  if (unresolved) console.warn(JSON.stringify({ stage: "render", warning: "dropped unresolved article_id references", count: unresolved }));
  return out;
}

// Google-News redirect links upgraded to the publisher URL, where the decoder found one.
export function applyDecodedLinks(selections: Selections, links: Record<string, string>): Selections {
  const decoded = (src: Source): Source => {
    const to = src.url && Object.hasOwn(links, src.url) ? links[src.url] : undefined;
    return to ? { ...src, url: to } : src;
  };
  const tier = (stories: Story[]) => stories.map((s) => ({ ...s, sources: s.sources.map(decoded) }));
  return { ...selections, must_know: tier(selections.must_know), should_know: tier(selections.should_know) };
}

// The join in digest.attach_thread_context: a story takes its cluster's thread context, unless two
// stories share the cluster, when the delta that replaces the summary would render both alike.
export function attachThreads(selections: Selections, contexts: Record<string, ThreadContext>): Selections {
  const counts = new Map<string, number>();
  for (const s of [...selections.must_know, ...selections.should_know]) if (s.cluster_id) counts.set(s.cluster_id, (counts.get(s.cluster_id) ?? 0) + 1);
  const tier = (stories: Story[]) =>
    stories.map((s) => {
      if (!s.cluster_id) return s;
      if ((counts.get(s.cluster_id) ?? 0) > 1) {
        console.error(JSON.stringify({ stage: "render", error: "cluster_id shared by several stories; thread context skipped", headline: s.headline, cluster_id: s.cluster_id }));
        return s;
      }
      const ctx = Object.hasOwn(contexts, s.cluster_id) ? contexts[s.cluster_id] : undefined;
      return ctx ? { ...s, thread: ctx } : s;
    });
  return { ...selections, must_know: tier(selections.must_know), should_know: tier(selections.should_know) };
}
