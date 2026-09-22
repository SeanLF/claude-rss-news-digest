import type { Pointer } from "../store/artifacts.js";

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
export interface DigestOutput {
  runId: number;
  stories: number;
  broadcast: "sent" | "rejected" | "skipped";
}

// The activity interface plan A2 fills, one function per stage; every model call and every
// network fetch is an activity, and each returns a pointer into the artifact store, never a blob.
export interface Activities {
  startRun(input: DigestInput): Promise<{ runId: number }>;
  fetchFeed(runId: number, sourceId: string): Promise<Pointer>;
  prepare(runId: number, fetched: Pointer[]): Promise<{ articles: Pointer[]; index: Pointer }>;
  planBatches(runId: number, articles: Pointer[]): Promise<{ batches: ExtractBatch[] }>;
  extractBatch(runId: number, batch: ExtractBatch, force?: boolean): Promise<Pointer>;
  joinClusters(runId: number, tagBatches: (Pointer | null)[], force?: boolean): Promise<Pointer>;
  recap(runId: number, force?: boolean): Promise<Pointer>;
  select(runId: number, clusters: Pointer, recap: Pointer, note?: string, input?: DigestInput): Promise<Pointer>;
  fulltext(runId: number, selected: Pointer): Promise<Pointer>;
  planStories(runId: number, selected: Pointer, clusters: Pointer): Promise<{ plans: StoryPlan[] }>;
  writeStory(runId: number, plan: StoryPlan, selected: Pointer, note?: string, force?: boolean): Promise<Pointer>;
  preheader(runId: number, drafts: Pointer[], force?: boolean): Promise<Pointer>;
  coherence(runId: number, drafts: Pointer[], fulltext: Pointer, note?: string, force?: boolean): Promise<Pointer>;
  repair(runId: number, drafts: Pointer[], report: Pointer): Promise<Pointer>;
  assemble(runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer | null): Promise<Pointer>;
  gnews(runId: number, selections: Pointer): Promise<Pointer>;
  threads(runId: number, selections: Pointer): Promise<Pointer>;
  render(runId: number, selections: Pointer, threads: Pointer, gnews: Pointer): Promise<{ html: Pointer; email: Pointer }>;
  broadcast(runId: number, email: Pointer): Promise<{ broadcastId: string }>;
  finishRun(runId: number, output: Omit<DigestOutput, "runId">): Promise<void>;
}
export const SOURCE_IDS_STUB: readonly string[] = ["reuters", "bbc_world", "al_jazeera"];
export const STORY_COUNT_STUB = 3;
