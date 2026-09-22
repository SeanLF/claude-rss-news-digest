export type StageTool = "Read" | "Grep";
export interface StageSpec {
  name: string;
  model: string;
  thinking: "adaptive" | "disabled";
  tools: StageTool[];
  body: string;
}

// Read and Grep on the input directory only (spec §2.2): publisher text reaches a model with
// nothing to mutate and nothing to exfiltrate to. Write is gone by contract: the result is the
// final message.
const ALLOWED: ReadonlySet<string> = new Set(["Read", "Grep"]);

export function parseAgentSpec(markdown: string): StageSpec {
  const parts = markdown.split("---");
  if (parts.length < 3) throw new Error("agent spec has no frontmatter");
  const front = parts[1] ?? "";
  const body = parts.slice(2).join("---").trim();
  const fields: Record<string, string> = {};
  for (const line of front.split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) fields[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
  const model = fields["model"];
  if (!model) throw new Error("agent spec has no model");
  const named = (fields["tools"] ?? "").split(/[,\s]+/).filter(Boolean);
  const bad = named.filter((t) => !ALLOWED.has(t));
  if (bad.length) throw new Error(`agent spec names tools the contract removed: ${bad.join(", ")}`);
  return { name: fields["name"] ?? "", model, thinking: fields["thinking"] === "adaptive" ? "adaptive" : "disabled", tools: named as StageTool[], body };
}

const TOKEN = /\{\{([^{}]*)\}\}/g;

export function renderBody(body: string, todayIso: string): string {
  const d = new Date(`${todayIso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) throw new Error(`not a date: ${todayIso}`);
  const pretty = d.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
  const out = body.replaceAll("{{CURRENT_DATE}}", pretty);
  const left = [...out.matchAll(TOKEN)].map((m) => m[1]);
  if (left.length) throw new Error(`unrendered token(s): ${[...new Set(left)].join(", ")}`);
  return out;
}
