import { describe, expect, it } from "vitest";
import type { Selections } from "../contracts/selections.js";
import { leakedIds } from "../contracts/leaks.js";
import { openDb } from "../store/db.js";
import { freshDb } from "../store/test-db.js";
import { preSendFailures, readPreSend, type PreSendInput } from "./pre-send.js";

const story = (headline: string, ids: string[], over: Record<string, unknown> = {}) => ({ headline, summary: `${headline} happened.`, why_it_matters: "It matters.", sources: ids.map((article_id) => ({ article_id })), ...over });
const brief = (headline: string, ids: string[], over: Record<string, unknown> = {}) => ({ headline, summary: `${headline} happened.`, sources: ids.map((article_id) => ({ article_id })), ...over });
const clean = (): Selections => ({
  must_know: [story("Ceasefire holds", ["A1", "A2"]), story("Rates cut", ["A3"])],
  should_know: [brief("Storm nears coast", ["A4"]), brief("Chip maker reports", ["A5"]), brief("Vote delayed", ["A6"])],
  preheader: "A ceasefire holds, rates are cut and a storm nears.",
});
const input = (over: Partial<PreSendInput> = {}): PreSendInput => {
  const selections = over.selections ?? clean();
  return { selections, draft: { must_know: selections.must_know, should_know: selections.should_know }, threadContext: {}, threadAuditFailures: 0, healthViolations: [], ...over };
};
const codes = (i: PreSendInput) => preSendFailures(i).map((f) => f.split(":")[0]);

describe("leakedIds (eval_graders._leaked_ids)", () => {
  it("quotes delimited groups, cited bare ids and cluster references, and passes an aircraft", () => {
    expect(leakedIds("Talks resumed (A12, A13) as NYT reported.", ["A99"])).toEqual(["(A12, A13)"]);
    expect(leakedIds("according to A238.", ["A238"])).toEqual(["A238"]);
    expect(leakedIds("the A19 chip and the A320 fleet", ["A1", "A3"])).toEqual([]);
    expect(leakedIds("as cluster 4 shows", [])).toEqual(["cluster 4"]);
  });
  it("counts an id inside its group once, and the same id again bare as a second place", () => {
    expect(leakedIds("(A7) said A7 later", ["A7"])).toEqual(["(A7)", "A7"]);
  });
});

describe("preSendFailures", () => {
  it("a clean run has none", () => {
    expect(preSendFailures(input())).toEqual([]);
  });
  it("an internal id in any reader-facing field fails, preheader first, each one quoted", () => {
    const s = clean();
    s.must_know[0]!.summary = "Talks resumed (A2) overnight.";
    s.should_know[1]!.reporting_varies = [{ source: "NYT (A5)", angle: "x", bias: "center" }];
    s.preheader = "Per A1, a ceasefire holds.";
    expect(preSendFailures(input({ selections: s }))).toEqual([
      "INTERNAL_ID_LEAK: 3 leak(s): preheader 'A1' | must_know.summary '(A2)' in 'Ceasefire holds' | should_know.reporting_varies.source '(A5)' in 'Chip maker reports'",
    ]);
  });
  it("a thread delta, which takes the summary's slot at render, is checked against its story's ids", () => {
    const s = clean();
    s.must_know[0]!.cluster_id = "ceasefire";
    const ctx = { ceasefire: { delta: "New today, per A2, talks resumed." } };
    expect(preSendFailures(input({ selections: s, threadContext: ctx }))).toEqual(["INTERNAL_ID_LEAK: 1 leak(s): thread delta 'A2' in 'Ceasefire holds'"]);
    // Two stories on one cluster: the render attaches neither delta, so none reaches readers.
    s.must_know[1]!.cluster_id = "ceasefire";
    expect(preSendFailures(input({ selections: s, threadContext: ctx }))).toEqual([]);
  });
  it("an empty headline or summary fails; a blanked why_it_matters is run-health's rule, not this one", () => {
    const s = clean();
    s.should_know[0]!.summary = "  ";
    s.must_know[1]!.why_it_matters = "";
    expect(preSendFailures(input({ selections: s }))).toEqual(["EMPTY_FIELD: 1 empty field(s): should_know.summary in 'Storm nears coast'"]);
  });
  it("story counts outside the L1 ranges fail", () => {
    const s = clean();
    s.should_know = s.should_know.slice(0, 2);
    expect(preSendFailures(input({ selections: s }))).toEqual(["STORY_COUNT: should_know=2 not in [3,14]"]);
  });
  it("a story COHERENCE flagged and repair could not save is named as dropped", () => {
    const i = input();
    i.draft!.must_know = [...i.draft!.must_know, story("Minister resigns", ["A9"])];
    expect(preSendFailures(i)).toEqual(["STORIES_DROPPED_AT_COHERENCE: 1 story(ies) failed the fact-check, were not repaired, and were dropped: 'Minister resigns'"]);
  });
  it("a repaired headline is not a drop: stories are matched by their cited ids", () => {
    const i = input();
    i.draft!.must_know = [story("Ceasefire collapses", ["A2", "A1"]), i.draft!.must_know[1]!];
    expect(preSendFailures(i)).toEqual([]);
  });
  it("a thread audit that failed open fails", () => {
    expect(preSendFailures(input({ threadAuditFailures: 2 }))).toEqual(["THREAD_AUDIT_FAILED: 2 thread update(s) shipped facts their audit could not check (it fails open)"]);
  });
  it("keeps the run-health rules that judge the content before the send, and only those", () => {
    const v = ["BLANKED_WHY_IT_MATTERS: 2 of 5 ...", "STORIES_DROPPED_AT_WRITE: 1 ...", "REPAIR_SPEC_ERROR: x", "ZERO_STORIES: y", "DEGRADED_CLUSTERING: z", "FULLTEXT_TOTAL_LOSS: w", "NO_THREAD_CONTINUATIONS: v"];
    expect(codes(input({ healthViolations: v }))).toEqual(["BLANKED_WHY_IT_MATTERS", "STORIES_DROPPED_AT_WRITE", "REPAIR_SPEC_ERROR"]);
  });
  it("without the draft or the thread trace, the checks that read them are skipped", () => {
    expect(preSendFailures(input({ draft: null, threadAuditFailures: null }))).toEqual([]);
  });
});

describe("readPreSend", () => {
  it("reads the run's current artifacts and its run health", async () => {
    const db = openDb(await freshDb([7]));
    const put = (name: string, content: unknown) => db.run("INSERT INTO artifacts (run_id, name, content, sha256, status, stage, kind) VALUES (7, $1, $2, repeat('0', 64), 'current', 'x', 'output')", [name, JSON.stringify(content)]);
    const s = clean();
    await put("selections.json", s);
    await put("draft_selections.json", { must_know: s.must_know, should_know: s.should_know, preheader: "" });
    await put("thread_health.json", { link: "ok", audit_failures: 1 });
    await put("thread_context.json", {});
    const got = await readPreSend(db, 7, { threadsEnabled: true });
    expect(got.selections).toEqual(s);
    expect(got.threadAuditFailures).toBe(1);
    expect(preSendFailures(got).map((f) => f.split(":")[0])).toEqual(["THREAD_AUDIT_FAILED"]);
  });
  it("a run with no assembled selections cannot be judged: it throws, and the workflow holds", async () => {
    const db = openDb(await freshDb([8]));
    await expect(readPreSend(db, 8, { threadsEnabled: true })).rejects.toThrow(/selections\.json/);
  });
});
