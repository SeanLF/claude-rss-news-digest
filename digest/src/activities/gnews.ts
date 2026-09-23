// Google-News link decoding at publish (spec §2.1): the decode is newsroom/src/gnews.py behind the
// Python worker's `decodeLinks` activity, since googlenewsdecoder has no TypeScript equivalent that
// reports a 429; choosing the links and storing the result stay here, over the survivors only.
import { resolveArticleIds, type Selections } from "../render/render.js";
import type { ArtifactStore, Pointer } from "../store/artifacts.js";
import { DECODED_LINKS, type GnewsDecode, type GnewsPlan } from "./index.js";

export const GNEWS_HEALTH = "gnews_health.json";
// A stored attempt that may have spent requests is kept on a resume, even a partial or failed one:
// the constraint is a per-IP daily budget. Only "unavailable" (no worker picked the decode up) and
// "disabled" reached no decoder, so only they are planned again.
const SETTLED = new Set(["completed", "no_candidates", "rate_limited", "deadline", "failed"]);
// digest._CANARY_MIN_ATTEMPTS: one undecodable article is not evidence the contract moved.
const CANARY_MIN_ATTEMPTS = 3;

export const isGnewsUrl = (url: string): boolean => url.includes("news.google.com") && url.includes("/articles/");

// The links digest._resolve_gnews_links decodes: the Google-News ones the rendered issue shows, after
// article ids are resolved and reposts collapsed, each once, in reading order.
export function survivingLinks(selections: Selections, index: Record<string, unknown>): string[] {
  const resolved = resolveArticleIds(selections, index);
  const urls = [...resolved.must_know, ...resolved.should_know].flatMap((s) => s.sources.map((src) => src.url ?? ""));
  return [...new Set(urls.filter(isGnewsUrl))];
}

// `enabled` is GNEWS_RESOLVE_ENABLED, the kill switch for when Google moves the RPC again.
export function gnewsActivities(deps: { store: ArtifactStore; enabled: boolean }) {
  const { store } = deps;
  return {
    async planGnews(runId: number, selections: Pointer, force = false): Promise<GnewsPlan> {
      const existing = store.find(runId, DECODED_LINKS);
      if (existing && !force) {
        const health = store.find(runId, GNEWS_HEALTH);
        const outcome = health ? (JSON.parse(store.get(health)) as { outcome?: unknown }).outcome : undefined;
        if (!health || SETTLED.has(String(outcome))) return { urls: [], existing };
        store.quarantine(runId, DECODED_LINKS);
        store.quarantine(runId, GNEWS_HEALTH);
      }
      if (!deps.enabled) return { urls: [], skip: "disabled" };
      const indexPtr = store.find(runId, "article_index.json");
      const index = indexPtr ? (JSON.parse(store.get(indexPtr)) as Record<string, unknown>) : {};
      const urls = survivingLinks(JSON.parse(store.get(selections)) as Selections, index);
      return urls.length ? { urls } : { urls, skip: "no_candidates" };
    },
    async storeGnews(runId: number, result: GnewsDecode, force = false): Promise<Pointer> {
      // gnews._fetch's own check, repeated where a string from another process becomes a link.
      const decoded = Object.fromEntries(Object.entries(result.decoded).filter(([, to]) => to.startsWith("http")));
      const upgraded = Object.keys(decoded).length;
      if (result.links && !upgraded && result.outcome !== "rate_limited" && result.attempted >= CANARY_MIN_ATTEMPTS) {
        console.warn(JSON.stringify({ stage: "gnews", warning: "upgraded 0 shown links; the decoder contract has probably moved again, check googlenewsdecoder for an update", links: result.links, attempted: result.attempted }));
      }
      const write = (name: string, text: string) => (force ? store.replace(runId, name, text) : store.put(runId, name, text));
      write(GNEWS_HEALTH, JSON.stringify({ links: result.links, decoded: upgraded, attempted: result.attempted, outcome: result.outcome }));
      return write(DECODED_LINKS, JSON.stringify(decoded, null, 2));
    },
  };
}
