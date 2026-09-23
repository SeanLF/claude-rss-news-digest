import { existsSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { Selections } from "../render/render.js";
import { survivingLinks } from "./gnews.js";

// Host-only: the production DB clone and the Python oracle are not in the CI image. The oracle is
// newsroom/tools/gnews_oracle.py DB RUN... run in the newsroom image, its stdout saved to GNEWS_ORACLE:
// the links digest._resolve_gnews_links handed gnews.resolve for each archived run.
const REPO = new URL("../../../", import.meta.url).pathname;
const DB = process.env["DIGEST_DB"] ?? `${REPO}data/digest.db`;
const ORACLE = process.env["GNEWS_ORACLE"] ?? `${REPO}data/replay/gnews-oracle.json`;
const ready = existsSync(DB) && existsSync(ORACLE);

describe.skipIf(!ready)("gnews plan parity with the Python", () => {
  const oracle = ready ? (JSON.parse(readFileSync(ORACLE, "utf8")) as Record<string, { render: string[]; prefetch: string[] }>) : {};
  const db = ready ? new DatabaseSync(DB, { readOnly: true }) : undefined;
  const artifact = (run: number, name: string) => (db!.prepare("SELECT content FROM run_artifacts WHERE run_id=? AND artifact_name=?").get(run, name) as { content: string }).content;
  it.each(Object.keys(oracle))("run %s decodes the links the Python's render pass decoded", (run) => {
    const id = Number(run);
    const ours = survivingLinks(JSON.parse(artifact(id, "selections.json")) as Selections, JSON.parse(artifact(id, "article_index.json")) as Record<string, unknown>);
    expect(ours).toEqual([...new Set(oracle[run]!.render)]);
    expect(ours.length).toBeLessThan(oracle[run]!.prefetch.length);
  });
});
