import type { AlertRequest } from "../ops/alerts.js";
import type { Pointer } from "../store/artifacts.js";
export type { AlertRequest };

// failStage is a test hook the stub honours; real activities ignore it.
export interface DigestInput {
  runDate: string;
  resumeRun?: number;
  force?: boolean;
  failStage?: "select";
}
// One extraction batch: which articles a model call tags. Sized by the data, so the fan-out is a
// workflow decision over a small payload, not a hidden loop inside one activity.
export interface ExtractBatch {
  index: number;
  ids: string[];
}
// One selected story to write: SELECT's order and tier, its citations, and the evidence it may see.
export interface StoryPlan {
  index: number;
  tier: "must_know" | "should_know";
  storyIds: string[];
  contextIds: string[];
  clusterIndex?: number;
}
export type FulltextTask = [articleId: string, url: string];
// What the Python fulltext activity returns: fulltext._collect_isolated's outcome, or "unavailable"
// when nothing answered on its queue.
export interface FulltextFetch { tasks: number; results: Record<string, string>; outcome: string }
// `existing`: the run already has its full text. `skip`: there is nothing to fetch, and why.
export interface FulltextPlan { tasks: FulltextTask[]; existing?: Pointer; skip?: "disabled" | "no_candidates" }
// The activity the Python worker serves on PYTHON_TASK_QUEUE.
export interface FulltextFetcher { fetchFulltext(tasks: FulltextTask[]): Promise<FulltextFetch> }
// What the Python `decodeLinks` activity returns: the links it was given, those it decoded, the decode
// requests it made, and how the pass ended ("completed", "rate_limited", "deadline", "cancelled"), or
// "unavailable" when nothing picked it up and "failed" when it broke after it started.
export interface GnewsDecode { links: number; decoded: Record<string, string>; attempted: number; outcome: string }
export interface GnewsPlan { urls: string[]; existing?: Pointer; skip?: "disabled" | "no_candidates" }
// The activity the Python worker serves on PYTHON_TASK_QUEUE for the decode.
export interface LinkDecoder { decodeLinks(urls: string[]): Promise<GnewsDecode> }
// THREADS: a continuing thread to synthesize, with its story's articles; what one synthesis
// reported; and what the workflow saw of the whole phase, which the finish records.
export interface ThreadPlan { threadId: number; articleIds: string[] }
export interface ThreadsLinked { plans: ThreadPlan[]; skip?: "disabled" }
export interface ThreadOutcome { threadId: number; auditFailed: boolean }
export interface ThreadsReport { linkError?: string; timedOut?: boolean; outcomes: ThreadOutcome[]; failures: { threadId: number; error: string }[] }
export interface FetchSummary { sourceId: string; ok: boolean; fetched: number; kept: number; error?: string }
export interface DigestOutput {
  runId: number;
  stories: number;
  // "disabled": BROADCAST_ENABLED is off, so nothing was published, like "rejected".
  // "held-out": the budget left no time for a review, so it was not sent unreviewed.
  broadcast: "sent" | "disabled" | "rejected" | "skipped" | "held-out";
  recipients?: number;
}

// The activity interface plan A2 fills, one function per stage; every model call and every
// network fetch is an activity, and each returns a pointer into the artifact store, never a blob.
export interface Activities {
  startRun(input: DigestInput): Promise<{ runId: number; sourceIds: string[]; lastRun: string | null }>;
  fetchFeed(runId: number, sourceId: string, lastRun: string | null): Promise<FetchSummary>;
  prepare(runId: number, fetched: FetchSummary[], force?: boolean): Promise<{ articles: Pointer[]; index: Pointer }>;
  planBatches(runId: number, articles: Pointer[]): Promise<{ batches: ExtractBatch[] }>;
  extractBatch(runId: number, batch: ExtractBatch, force?: boolean): Promise<Pointer>;
  joinClusters(runId: number, tagBatches: (Pointer | null)[], force?: boolean): Promise<Pointer>;
  recap(runId: number, force?: boolean): Promise<Pointer>;
  select(runId: number, clusters: Pointer, recap: Pointer, note?: string, input?: DigestInput): Promise<Pointer>;
  planFulltext(runId: number, selected: Pointer, force?: boolean): Promise<FulltextPlan>;
  storeFulltext(runId: number, fetched: FulltextFetch, force?: boolean): Promise<Pointer>;
  planStories(runId: number, selected: Pointer, clusters: Pointer): Promise<{ plans: StoryPlan[] }>;
  writeStory(runId: number, plan: StoryPlan, selected: Pointer, note?: string, force?: boolean): Promise<Pointer>;
  preheader(runId: number, drafts: Pointer[], force?: boolean): Promise<Pointer>;
  coherence(runId: number, drafts: Pointer[], fulltext: Pointer, note?: string, force?: boolean): Promise<Pointer>;
  repair(runId: number, drafts: Pointer[], report: Pointer, force?: boolean): Promise<Pointer>;
  assemble(runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer | null, force?: boolean): Promise<Pointer>;
  planGnews(runId: number, selections: Pointer, force?: boolean): Promise<GnewsPlan>;
  storeGnews(runId: number, decoded: GnewsDecode, force?: boolean): Promise<Pointer>;
  threadsLink(runId: number, force?: boolean): Promise<ThreadsLinked>;
  threadSynthesis(runId: number, plan: ThreadPlan): Promise<ThreadOutcome>;
  threadsFinish(runId: number, report: ThreadsReport): Promise<Pointer>;
  threadsRetract(runId: number): Promise<{ retracted: boolean; reason?: string }>;
  render(runId: number, selections: Pointer, threads: Pointer, gnews: Pointer): Promise<{ html: Pointer; email: Pointer }>;
  archiveRun(runId: number, selections: Pointer, clusters: Pointer): Promise<void>;
  sendEnabled(): Promise<boolean>;
  // holdEndsAt null: no run budget was left for a hold, and the send follows at once.
  notifyHold(runId: number, selections: Pointer, holdEndsAt: string | null): Promise<{ sent: boolean }>;
  saveDigest(runId: number, html: Pointer, selections: Pointer): Promise<{ date: string }>;
  broadcast(runId: number, email: Pointer): Promise<{ broadcastId: string; status: string; recipients: number }>;
  recordShownHeadlines(runId: number, selections: Pointer): Promise<{ rows: number }>;
  finishRun(runId: number, output: Omit<DigestOutput, "runId">): Promise<void>;
  // Operations (src/activities/ops.ts): best-effort, none can fail a run.
  weeklyRecap(runId: number, force?: boolean): Promise<Pointer | null>;
  healthcheck(event: "start" | "success" | "fail", note?: string): Promise<void>;
  healthcheckLog(message: string): Promise<void>;
  checkFeeds(runId: number, sourceIds: string[]): Promise<AlertRequest | null>;
  checkRunHealth(runId: number, broadcasting: boolean): Promise<AlertRequest | null>;
  alert(req: AlertRequest): Promise<void>;
  abortRun(runId: number, error: string): Promise<void>;
}
export const STORY_COUNT_STUB = 3;
// What threads and gnews hand render: each story's thread context by cluster label, and each
// decoded Google-News link. Named apart from the Python's thread_links.json trace, which a resumed
// run's archive already holds.
export const THREAD_CONTEXT = "thread_context.json";
export const DECODED_LINKS = "gnews_links.json";
