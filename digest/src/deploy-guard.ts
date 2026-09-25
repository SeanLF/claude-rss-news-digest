import type { Client } from "@temporalio/client";

// A deploy restarts the one worker a run is pinned to (deployment.ts), and a paused schedule drops the
// slot it misses, so a deploy is refused from 12:00 (before the 12:25 start, client.ts) to 13:45
// Europe/Paris (past a normal run's end), and while a DigestWorkflow runs.
const WINDOW = { from: 1200, to: 1345 };

function parisHhmm(now: Date): number {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(now);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  return get("hour") * 100 + get("minute");
}

export const inRunWindow = (now: Date): boolean => {
  const t = parisHhmm(now);
  return t >= WINDOW.from && t < WINDOW.to;
};

export const RUNNING_DIGESTS_QUERY = 'WorkflowType="DigestWorkflow" AND ExecutionStatus="Running"';

// A run whose workflow task keeps failing (a nondeterminism error) carries the server's
// TemporalReportedProblems search attribute; it cannot take a signal, so it is named apart.
export async function runningDigests(client: Client): Promise<string[]> {
  const ids: string[] = [];
  for await (const w of client.workflow.list({ query: RUNNING_DIGESTS_QUERY })) {
    const stuck = "TemporalReportedProblems" in (w.raw.searchAttributes?.indexedFields ?? {});
    ids.push(w.workflowId + (stuck ? " (stuck: its workflow task keeps failing)" : ""));
  }
  return ids;
}

// Every reason to refuse, empty when a deploy may go ahead. A listing that fails is a reason too.
export async function guard(client: Client, now = new Date()): Promise<string[]> {
  const problems: string[] = [];
  if (inRunWindow(now)) {
    const t = String(parisHhmm(now)).padStart(4, "0");
    problems.push(`${t.slice(0, 2)}:${t.slice(2)} Europe/Paris is inside the run window (12:00-13:45 Europe/Paris)`);
  }
  try {
    const running = await runningDigests(client);
    if (running.length) problems.push(`digest workflow(s) running: ${running.join(", ")}`);
  } catch (e) {
    problems.push(`could not list the running digest workflows: ${String(e)}`);
  }
  return problems;
}
