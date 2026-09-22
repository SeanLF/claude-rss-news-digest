import { defineSignal } from "@temporalio/workflow";
// The three human-in-the-loop signals (spec §2.3): the pre-broadcast hold, the retries-exhausted
// park, and an operator note injected into a stage's next attempt.
export const approveSignal = defineSignal<[{ decision: "approve" | "reject" }]>("approve");
export const retrySignal = defineSignal<[{ decision: "retry" | "abort" }]>("retry");
export const operatorNoteSignal = defineSignal<[{ stage: string; note: string }]>("operatorNote");
