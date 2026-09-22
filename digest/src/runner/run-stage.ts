import { query, type Options, type SDKMessage, type ThinkingConfig } from "@anthropic-ai/claude-agent-sdk";
import { renderBody, type StageSpec } from "./prompt.js";

export interface StageInput {
  userMessage: string;
  inputDir: string;
}
export interface StageResult {
  text: string;
  structured?: unknown;
  toolCalls: { name: string; target: string }[];
  costUsd: number;
  usage: Record<string, number>;
  durationMs: number;
  numTurns: number;
}
export type SdkQuery = typeof query;

// `tools` is the SDK's base set of built-ins and the only option that restricts availability;
// `allowedTools` merely skips the permission prompt. The disallow list is belt and braces for
// the tools a preset would otherwise add.
const BUILTIN = ["Read", "Grep", "Glob", "Write", "Edit", "MultiEdit", "Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit", "TodoWrite", "ExitPlanMode", "BashOutput", "KillBash", "SlashCommand", "ListMcpResources", "ReadMcpResource", "ReadMcpResourceDir"] as const;

function targetOf(name: string, input: unknown): string {
  const inp = (input && typeof input === "object" ? input : {}) as Record<string, unknown>;
  const v = name === "Grep" ? inp["pattern"] : inp["file_path"];
  return typeof v === "string" ? v : "";
}

function controllerFor(signal: AbortSignal): AbortController {
  const c = new AbortController();
  if (signal.aborted) c.abort(signal.reason);
  else signal.addEventListener("abort", () => c.abort(signal.reason), { once: true });
  return c;
}

const thinkingFor = (t: StageSpec["thinking"]): ThinkingConfig => (t === "adaptive" ? { type: "adaptive" } : { type: "disabled" });

// One stage: system prompt from the spec, the stage's input as the user turn, tools scoped to
// Read and Grep over the input directory, the result as the final message (structured when a
// schema is given). Tool calls are recorded so a checker's "no FAIL without a Grep behind it"
// can be checked in the transcript rather than asked for in prose (spec §2.2).
export async function runStage(
  spec: StageSpec,
  input: StageInput,
  opts: { outputSchema?: Record<string, unknown>; today: string; query?: SdkQuery; maxBudgetUsd?: number; heartbeat?: () => void; signal?: AbortSignal },
): Promise<StageResult> {
  const q = opts.query ?? query;
  const allowed: readonly string[] = spec.tools;
  const options: Options = {
    model: spec.model,
    systemPrompt: renderBody(spec.body, opts.today),
    cwd: input.inputDir,
    tools: [...allowed],
    allowedTools: [...allowed],
    disallowedTools: BUILTIN.filter((t) => !allowed.includes(t)),
    permissionMode: "acceptEdits",
    thinking: thinkingFor(spec.thinking),
    // cwd alone does not confine Read or Grep: an absolute path reaches the whole disk. This does.
    settings: { permissions: { blockReadsOutsideWorkingDirectories: true } },
    // A cancelled activity (its workflow terminated or cancelled) stops the model call; otherwise a
    // zombie attempt keeps running and can still write its artifact after the run is gone.
    ...(opts.signal ? { abortController: controllerFor(opts.signal) } : {}),
    ...(opts.maxBudgetUsd !== undefined ? { maxBudgetUsd: opts.maxBudgetUsd } : {}),
    ...(opts.outputSchema ? { outputFormat: { type: "json_schema", schema: opts.outputSchema } } : {}),
  };
  const toolCalls: { name: string; target: string }[] = [];
  const texts: string[] = [];
  let result: Extract<SDKMessage, { type: "result" }> | undefined;
  // A model call can outlast the activity's heartbeat timeout; beat on every message and every 30 s.
  const beat = opts.heartbeat;
  const timer = beat ? setInterval(beat, 30_000) : undefined;
  try {
  for await (const m of q({ prompt: input.userMessage, options })) {
    beat?.();
    if (m.type === "assistant") {
      for (const block of m.message.content) {
        if (block.type === "tool_use") toolCalls.push({ name: block.name, target: targetOf(block.name, block.input) });
        else if (block.type === "text") texts.push(block.text);
      }
    } else if (m.type === "result") {
      result = m;
    }
  }
  } finally {
    if (timer) clearInterval(timer);
  }
  if (!result) throw new Error(`stage ${spec.name}: no result message`);
  if (result.subtype !== "success" || result.is_error) throw new Error(`stage ${spec.name}: ${result.subtype}`);
  // The SDK re-prompts on schema mismatch and ends with error_max_structured_output_retries; a
  // success WITHOUT structured_output when a schema was requested is also a failure.
  const structured = result.structured_output;
  if (opts.outputSchema && structured === undefined) throw new Error(`stage ${spec.name}: success without structured output`);
  const usage: Record<string, number> = {};
  for (const [k, v] of Object.entries(result.usage)) if (typeof v === "number") usage[k] = v;
  return {
    text: (result.result || texts.join("\n")).trim(),
    ...(structured !== undefined ? { structured } : {}),
    toolCalls,
    costUsd: result.total_cost_usd,
    usage,
    durationMs: result.duration_ms,
    numTurns: result.num_turns,
  };
}
