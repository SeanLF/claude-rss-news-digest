import { ApplicationFailure } from "@temporalio/common";
import type { Pointer } from "../store/artifacts.js";
import { STORY_COUNT_STUB, type Activities, type DigestInput } from "./index.js";

const ptr = (runId: number, name: string): Pointer => ({ runId, name, sha256: "0".repeat(64) });

// Every activity returns a plausible pointer and nothing else, so the workflow's sequencing,
// signals, identity and budget can be exercised end to end before any stage is real.
export function stubActivities(): Activities {
  return {
    startRun: (input: DigestInput) => Promise.resolve({ runId: input.resumeRun ?? 1 }),
    fetchFeed: (runId, sourceId) => Promise.resolve(ptr(runId, `feed_${sourceId}.json`)),
    prepare: (runId) => Promise.resolve({ articles: [ptr(runId, "articles_1.csv")], index: ptr(runId, "article_index.json") }),
    cluster: (runId) => Promise.resolve(ptr(runId, "clusters.json")),
    recap: (runId) => Promise.resolve(ptr(runId, "recap.txt")),
    select: (runId, _clusters, _recap, _note, input) => {
      if (input?.failStage === "select") return Promise.reject(ApplicationFailure.nonRetryable("select failed for the test", "StubFailure"));
      return Promise.resolve(ptr(runId, "selected.json"));
    },
    fulltext: (runId) => Promise.resolve(ptr(runId, "article_fulltext.json")),
    storyCount: () => Promise.resolve(STORY_COUNT_STUB),
    writeStory: (runId, i) => Promise.resolve(ptr(runId, `draft_${i}.json`)),
    preheader: (runId) => Promise.resolve(ptr(runId, "preheader.txt")),
    coherence: (runId) => Promise.resolve(ptr(runId, "coherence_report.json")),
    repair: (runId) => Promise.resolve(ptr(runId, "repair_resolution.json")),
    assemble: (runId) => Promise.resolve(ptr(runId, "selections.json")),
    gnews: (runId) => Promise.resolve(ptr(runId, "gnews.json")),
    threads: (runId) => Promise.resolve(ptr(runId, "thread_links.json")),
    render: (runId) => Promise.resolve({ html: ptr(runId, "digest.html"), email: ptr(runId, "digest.mjml.html") }),
    broadcast: () => Promise.resolve({ broadcastId: "stub" }),
    finishRun: () => Promise.resolve(),
  };
}
