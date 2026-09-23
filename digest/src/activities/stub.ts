import { ApplicationFailure } from "@temporalio/common";
import type { Pointer } from "../store/artifacts.js";
import { DECODED_LINKS, STORY_COUNT_STUB, THREAD_CONTEXT, type Activities, type DigestInput } from "./index.js";

const ptr = (runId: number, name: string): Pointer => ({ runId, name, sha256: "0".repeat(64) });

// Every activity returns a plausible pointer and nothing else, so the workflow's sequencing,
// signals, identity and budget can be exercised end to end before any stage is real.
export function stubActivities(): Activities {
  return {
    startRun: (input: DigestInput) => Promise.resolve({ runId: input.resumeRun ?? 1, sourceIds: ["reuters", "bbc_world", "al_jazeera"], lastRun: null }),
    fetchFeed: (_runId, sourceId) => Promise.resolve({ sourceId, ok: true, fetched: 1, kept: 1 }),
    prepare: (runId) => Promise.resolve({ articles: [ptr(runId, "articles_1.csv")], index: ptr(runId, "article_index.json") }),
    planBatches: () => Promise.resolve({ batches: [{ index: 0, ids: ["A1"] }] }),
    extractBatch: (runId, b) => Promise.resolve(ptr(runId, `cluster_tags_b${b.index}.json`)),
    joinClusters: (runId) => Promise.resolve(ptr(runId, "clusters.json")),
    recap: (runId) => Promise.resolve(ptr(runId, "recap.txt")),
    select: (runId, _clusters, _recap, _note, input) => {
      if (input?.failStage === "select") return Promise.reject(ApplicationFailure.nonRetryable("select failed for the test", "StubFailure"));
      return Promise.resolve(ptr(runId, "selected.json"));
    },
    planFulltext: () => Promise.resolve({ tasks: [["A1", "https://example.com/a1"]] }),
    storeFulltext: (runId) => Promise.resolve(ptr(runId, "article_fulltext.json")),
    planStories: () => Promise.resolve({ plans: Array.from({ length: STORY_COUNT_STUB }, (_, i) => ({ index: i, tier: "must_know" as const, storyIds: ["A1"], contextIds: ["A1"] })) }),
    writeStory: (runId, plan) => Promise.resolve(ptr(runId, `draft_s${plan.index}.json`)),
    preheader: (runId) => Promise.resolve(ptr(runId, "preheader.txt")),
    coherence: (runId) => Promise.resolve(ptr(runId, "coherence_report.json")),
    repair: (runId) => Promise.resolve(ptr(runId, "repair_resolution.json")),
    assemble: (runId) => Promise.resolve(ptr(runId, "selections.json")),
    gnews: (runId) => Promise.resolve(ptr(runId, DECODED_LINKS)),
    threads: (runId) => Promise.resolve(ptr(runId, THREAD_CONTEXT)),
    render: (runId) => Promise.resolve({ html: ptr(runId, "digest.html"), email: ptr(runId, "email.html") }),
    broadcast: () => Promise.resolve({ broadcastId: "stub" }),
    finishRun: () => Promise.resolve(),
    weeklyRecap: (runId) => Promise.resolve(ptr(runId, "weekly_recap.txt")),
    healthcheck: () => Promise.resolve(),
    healthcheckLog: () => Promise.resolve(),
    checkFeeds: () => Promise.resolve(null),
    checkRunHealth: () => Promise.resolve(null),
    alert: () => Promise.resolve(),
  };
}
