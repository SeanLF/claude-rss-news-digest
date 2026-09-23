import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// These prompts are carried verbatim so their behaviour cannot drift from production. The
// production files live outside digest/, so this runs where the repo is checked out and is skipped
// in the ci-ts image, which carries digest/ only.
const prod = (name: string) => new URL(`../../.claude/agents/${name}.md`, import.meta.url).pathname;
const body = (p: string) => readFileSync(p, "utf8").split("---").slice(2).join("---").trim();

describe.skipIf(!existsSync(prod("coherence")))("agent prompt parity with production", () => {
  it.each(["coherence", "thread-link", "thread-synthesis", "thread-audit"])("%s.md body is identical", (name) => {
    expect(body(new URL(`../agents/${name}.md`, import.meta.url).pathname)).toBe(body(prod(name)));
  });
});
