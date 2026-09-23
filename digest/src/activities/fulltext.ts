// Fulltext on both sides of the language line (docs/2026-09-23-fulltext-extractor-fork.md): the fetch
// and trafilatura's extract are a Python activity on the `fulltext` task queue; planning the tasks
// and storing the result stay here, so the artifact store has one writer language.
import { scrubUrls } from "../contracts/ids.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import type { FulltextFetch, FulltextPlan, FulltextTask } from "./index.js";

export const FULLTEXT_OUTPUT = "article_fulltext.json";
export const FULLTEXT_HEALTH = "fulltext_health.json";
// Outcomes that settle the step. Anything else (the fetcher unavailable, killed or crashed, or the
// switch off at the time) is retried on a resume, as production refetches on every call.
const SETTLED = new Set(["completed", "no_candidates"]);

// fulltext._candidate_article_ids: SELECT lists the representative articles first, so a prefix
// favours the best-covered sources.
export function candidateIds(selected: unknown, perStory: number): string[] {
  const seen = new Set<string>();
  const sel = (selected ?? {}) as Record<string, unknown>;
  for (const tier of ["must_know", "should_know"]) {
    const stories = sel[tier];
    if (!Array.isArray(stories)) continue;
    for (const story of stories) {
      const ids = (story as { article_ids?: unknown } | null)?.article_ids;
      if (!Array.isArray(ids)) continue;
      for (const id of ids.slice(0, perStory)) if (typeof id === "string") seen.add(id);
    }
  }
  return [...seen];
}

// `enabled` is FULLTEXT_ENABLED, the switch production's run-281 recovery turns off.
export function fulltextActivities(deps: { store: ArtifactStore; perStory: number; enabled: boolean }) {
  const { store } = deps;
  return {
    async planFulltext(runId: number, selected: Pointer, force = false): Promise<FulltextPlan> {
      const existing = store.find(runId, FULLTEXT_OUTPUT);
      if (existing && !force) {
        const health = store.find(runId, FULLTEXT_HEALTH);
        const outcome = health ? (JSON.parse(store.get(health)) as { outcome?: unknown }).outcome : undefined;
        // An archived output with no health record is kept: nothing says it failed.
        if (!health || SETTLED.has(String(outcome))) return { tasks: [], existing };
        store.quarantine(runId, FULLTEXT_OUTPUT);
        store.quarantine(runId, FULLTEXT_HEALTH);
      }
      if (!deps.enabled) return { tasks: [], skip: "disabled" };
      const indexPtr = store.find(runId, "article_index.json");
      const index = indexPtr ? (JSON.parse(store.get(indexPtr)) as Record<string, { url?: unknown } | undefined>) : {};
      const tasks = candidateIds(JSON.parse(store.get(selected)), deps.perStory).flatMap((id): FulltextTask[] => {
        const url = index[id]?.url;
        return typeof url === "string" && url ? [[id, url]] : [];
      });
      return tasks.length ? { tasks } : { tasks, skip: "no_candidates" };
    },
    // Links are scrubbed here, at the source, as prepare scrubs the summaries: no URL reaches a model.
    async storeFulltext(runId: number, fetched: FulltextFetch, force = false): Promise<Pointer> {
      const payload = Object.fromEntries(Object.entries(fetched.results).map(([id, text]) => [id, { text: scrubUrls(text) }]));
      const write = (name: string, text: string) => (force ? store.replace(runId, name, text) : store.put(runId, name, text));
      write(FULLTEXT_HEALTH, JSON.stringify({ tasks: fetched.tasks, extracted: Object.keys(payload).length, outcome: fetched.outcome }));
      return write(FULLTEXT_OUTPUT, Object.keys(payload).length ? JSON.stringify(payload, null, 2) : "{}");
    },
  };
}
