import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import type { JudgeVerdict } from "./verdict.js";

// A whole-digest judge is a CLI from one model family (spec §7 item 2): it gets {rubric, digest,
// inputsDir} as JSON on stdin and prints a JSON array of verdicts. Two families judge; their
// disagreements go to Sean.
export interface Judge {
  name: string;
  family: "anthropic" | "openai" | "google";
  run(digestHtml: string, inputsDir: string): Promise<JudgeVerdict[]>;
}
export const RUBRIC = readFileSync(new URL("../../gate/rubric.md", import.meta.url), "utf8");

// The outermost [...] in chatty stdout, validated cell by cell.
export function parseVerdicts(stdout: string): JudgeVerdict[] {
  const start = stdout.indexOf("[");
  const end = stdout.lastIndexOf("]");
  if (start < 0 || end < start) throw new Error("judge returned no JSON array");
  const parsed: unknown = JSON.parse(stdout.slice(start, end + 1));
  if (!Array.isArray(parsed)) throw new Error("judge returned no JSON array");
  return parsed.map((v: unknown, i) => {
    const o = (v ?? {}) as Record<string, unknown>;
    const { story, criterion, pass, reason } = o;
    if (typeof story !== "number" || typeof criterion !== "number" || criterion < 1 || criterion > 7 || typeof pass !== "boolean")
      throw new Error(`judge verdict ${i} is malformed: ${JSON.stringify(v)}`);
    return { story, criterion: criterion as JudgeVerdict["criterion"], pass, reason: typeof reason === "string" ? reason : "" };
  });
}

export function cliJudge(name: string, family: Judge["family"], command: string[]): Judge {
  return {
    name,
    family,
    run: (digestHtml, inputsDir) =>
      new Promise((resolve, reject) => {
        const [cmd, ...args] = command;
        if (!cmd) return reject(new Error("empty judge command"));
        // A nested Claude Code session refuses `claude -p`; the judge is its own process.
        const { CLAUDECODE: _c, ...env } = process.env;
        const p = spawn(cmd, args, { stdio: ["pipe", "pipe", "inherit"], env });
        let out = "";
        p.stdout.on("data", (d: Buffer) => {
          out += d.toString();
        });
        p.on("error", reject);
        p.on("close", (code) => {
          if (code !== 0) return reject(new Error(`${name} exited ${code}`));
          try {
            resolve(parseVerdicts(out));
          } catch (e) {
            reject(e instanceof Error ? e : new Error(String(e)));
          }
        });
        p.stdin.end(JSON.stringify({ rubric: RUBRIC, digest: digestHtml, inputsDir }));
      }),
  };
}
