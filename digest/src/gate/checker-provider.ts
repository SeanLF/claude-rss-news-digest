// A promptfoo provider that runs the TypeScript checker over a fixture directory (a draft plus the
// day's CSVs and fulltext), for the planted-defect band. The report is the output; promptfoo's
// --repeat makes the reps and planted-assert.ts scores each against the key.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { checkDraft } from "../activities/coherence.js";

interface Config { fixtureDir: string; today: string; agentsDir?: string }

export default class CheckerProvider {
  private readonly config: Config;
  constructor(options: { config?: Partial<Config> } = {}) {
    const c = options.config ?? {};
    if (!c.fixtureDir || !c.today) throw new Error("checker provider needs config.fixtureDir and config.today");
    this.config = { fixtureDir: c.fixtureDir, today: c.today, agentsDir: c.agentsDir ?? new URL("../../agents/", import.meta.url).pathname };
  }
  id(): string {
    return "ts-checker";
  }
  async callApi() {
    const dir = this.config.fixtureDir;
    const corpus = readdirSync(dir)
      .filter((n) => /^articles_\d+\.csv$/.test(n) || n === "article_fulltext.json")
      .toSorted()
      .map((n): [string, string] => [n, readFileSync(join(dir, n), "utf8")]);
    const t0 = Date.now();
    const r = await checkDraft({ agentsDir: this.config.agentsDir! }, readFileSync(join(dir, "draft_selections.json"), "utf8"), corpus, this.config.today);
    return { output: JSON.stringify(r.report), cost: r.costUsd, latencyMs: Date.now() - t0, metadata: { numTurns: r.numTurns, toolCalls: r.toolCalls, unbackedFails: r.unbacked } };
  }
}
