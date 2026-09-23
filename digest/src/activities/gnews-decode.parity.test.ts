import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { GnewsDecode } from "./index.js";
import { linkDecoderFromEnv } from "./gnews-decode.js";

// Live, and host-only: it spends the address's Google News budget, so it never runs in CI. The oracle
// is the Python `decodeLinks` activity (googlenewsdecoder fork) over runs 300-305's shown links,
// recorded by digest/python/decode_oracle.py, removed with that activity (docs/proposed/2026-09-23-gnews-parity).
// GNEWS_LIVE=1 runs it; GNEWS_PARITY_OUT saves what this side decoded.
const REPO = new URL("../../../", import.meta.url).pathname;
const ORACLE = process.env["GNEWS_ORACLE"] ?? `${REPO}docs/proposed/2026-09-23-gnews-parity/python-decode.json`;
const live = process.env["GNEWS_LIVE"] === "1" && existsSync(ORACLE);

describe.skipIf(!live)("decodeLinks parity with the Python activity, live", () => {
  const oracle = live ? (JSON.parse(readFileSync(ORACLE, "utf8")) as Record<string, { links: string[]; python: GnewsDecode }>) : {};
  const ours: Record<string, GnewsDecode> = {};
  const decoder = linkDecoderFromEnv(process.env);
  it.each(Object.keys(oracle))("run %s: the same links decode to the same publisher URLs", async (run) => {
    const { links, python } = oracle[run]!;
    ours[run] = await decoder.decodeLinks(links);
    const out = process.env["GNEWS_PARITY_OUT"];
    if (out) writeFileSync(out, JSON.stringify(ours, null, 2));
    expect(ours[run]).toEqual(python);
  }, 300_000);
});
