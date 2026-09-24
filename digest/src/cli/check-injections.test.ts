import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadAssets } from "../site/assets.js";
import { testConfig } from "../site/testing.js";
import { checkInjections } from "./check-injections.js";

// The pipeline's web template, whose markup the issue page's injections are anchored to.
const TEMPLATE = readFileSync("/app/newsroom/templates/digest-template.html", "utf8");
const GOOD = TEMPLATE.replace("{{STYLES}}", "body{}").replaceAll(/\{\{[A-Z_]+\}\}/g, "");
const cfg = testConfig();
const ctx = { cfg, assets: loadAssets(cfg.designDir) };
const stored = (html: string) => ({ html, preheader: "" });

describe("check-injections", () => {
  it("finds nothing to report in an issue rendered from the current template", async () => {
    expect(await checkInjections(ctx, ["2026-09-01"], async () => stored(GOOD))).toEqual([]);
  });

  it("reports each date whose stored HTML lacks a needle, with the needles it missed", async () => {
    const noPaper = GOOD.replace('<div class="paper">', '<div class="sheet">');
    const noFooter = GOOD.replace('<p class="footer-meta">', "<p>").replace("</footer>", "</div>");
    const blobs: Record<string, string> = { "2026-09-01": GOOD, "2026-09-02": noPaper, "2026-09-03": noFooter };
    const misses = await checkInjections(ctx, Object.keys(blobs), async (d) => stored(blobs[d]!));
    expect(misses).toEqual([
      { date: "2026-09-02", needles: ['<div class="paper">'] },
      { date: "2026-09-03", needles: ["</footer>"] },
    ]);
  });

  it("reports a date the database lists but cannot return", async () => {
    expect(await checkInjections(ctx, ["2026-09-04"], async () => undefined)).toEqual([{ date: "2026-09-04", needles: [], error: "no stored issue" }]);
  });
});
