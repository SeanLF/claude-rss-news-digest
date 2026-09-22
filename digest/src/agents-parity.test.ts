import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The checker's prompt is carried verbatim so its probes cannot drift from production. The
// production file lives outside digest/, so this runs where the repo is checked out and is skipped
// in the ci-ts image, which carries digest/ only.
const prod = new URL("../../.claude/agents/coherence.md", import.meta.url).pathname;
const body = (p: string) => readFileSync(p, "utf8").split("---").slice(2).join("---").trim();

describe.skipIf(!existsSync(prod))("agent prompt parity with production", () => {
  it("coherence.md body is identical", () => {
    expect(body(new URL("../agents/coherence.md", import.meta.url).pathname)).toBe(body(prod));
  });
});
