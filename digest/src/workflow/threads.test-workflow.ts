import type { Duration } from "@temporalio/common";
import type { Pointer } from "../store/artifacts.js";
import { threadsPhase } from "./threads.js";

// The threads phase alone, with a bound a test can reach in real time: the test server does not
// skip time while an activity is running, so a 30-minute bound cannot be hit there.
export async function ThreadsPhaseProbe(runId: number, bound: Duration): Promise<Pointer> {
  return threadsPhase(runId, false, bound);
}
