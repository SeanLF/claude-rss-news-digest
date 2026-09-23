import { createHash } from "node:crypto";
import type { ArtifactStore } from "../store/artifacts.js";

// An operator note changes the run, so it is an input artifact (spec §2.3). Named by its content, so
// a retried attempt, or parallel WRITE branches carrying the same note, land on one row with no
// counter to race on; arrival order is the rows' created_at.
export function recordOperatorNote(store: ArtifactStore, runId: number, stage: string, note: string | undefined): void {
  if (!note?.trim()) return;
  const digest = createHash("sha256").update(note).digest("hex").slice(0, 12);
  store.put(runId, `operator_note.${stage}.${digest}.txt`, note);
}
