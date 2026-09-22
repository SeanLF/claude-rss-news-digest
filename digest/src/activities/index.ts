import type { Pointer } from "../store/artifacts.js";

// failStage is a test hook the stub honours; real activities ignore it.
export interface DigestInput {
  runDate: string;
  resumeRun?: number;
  force?: boolean;
  failStage?: "select";
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
  cluster(runId: number, articles: Pointer[]): Promise<Pointer>;
  recap(runId: number): Promise<Pointer>;
  select(runId: number, clusters: Pointer, recap: Pointer, note?: string, input?: DigestInput): Promise<Pointer>;
  fulltext(runId: number, selected: Pointer): Promise<Pointer>;
  storyCount(runId: number, selected: Pointer): Promise<number>;
  writeStory(runId: number, storyIndex: number, selected: Pointer, fulltext: Pointer, note?: string): Promise<Pointer>;
  preheader(runId: number, drafts: Pointer[]): Promise<Pointer>;
  coherence(runId: number, drafts: Pointer[], fulltext: Pointer, note?: string): Promise<Pointer>;
  repair(runId: number, drafts: Pointer[], report: Pointer): Promise<Pointer>;
  assemble(runId: number, drafts: Pointer[], report: Pointer, repair: Pointer, preheader: Pointer): Promise<Pointer>;
  gnews(runId: number, selections: Pointer): Promise<Pointer>;
  threads(runId: number, selections: Pointer): Promise<Pointer>;
  render(runId: number, selections: Pointer, threads: Pointer, gnews: Pointer): Promise<{ html: Pointer; email: Pointer }>;
  broadcast(runId: number, email: Pointer): Promise<{ broadcastId: string }>;
  finishRun(runId: number, output: Omit<DigestOutput, "runId">): Promise<void>;
}
export const SOURCE_IDS_STUB: readonly string[] = ["reuters", "bbc_world", "al_jazeera"];
export const STORY_COUNT_STUB = 3;
