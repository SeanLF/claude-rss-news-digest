import { describe, expect, it } from "vitest";
import { answerFor, articleSignature, expandNeighbourhood, synthesisPrompt, type Art } from "./synthesis.js";

const arts = (o: Record<string, Art>) => new Map(Object.entries(o));

// Cases carried from newsroom/tests/test_thread_synthesis.py.
describe("expandNeighbourhood", () => {
  it("pulls entity neighbours, not unrelated stories", () => {
    const out = expandNeighbourhood(["A1"], arts({
      A1: { title: "Iran and US sign nuclear ceasefire in Geneva", summary: "Iran deal." },
      A2: { title: "Iran tankers transit Strait of Hormuz", summary: "Iran shipping resumes." },
      A3: { title: "Brazil holds carnival parade in Rio", summary: "Unrelated festival." },
    }), 0.1, 5);
    expect(out).toEqual(["A1", "A2"]);
  });
  it("respects the cap, breaking score ties toward the larger id as Python's reverse tuple sort does", () => {
    const a = arts(Object.fromEntries(Array.from({ length: 6 }, (_, i) => [`A${i}`, { title: "Iran nuclear deal talks", summary: "Iran" }])));
    expect(expandNeighbourhood(["A0"], a, 0.5, 2)).toEqual(["A0", "A5", "A4"]);
  });
  it("returns the seed when it has no signal", () => {
    expect(expandNeighbourhood(["A1"], arts({ A1: { title: "", summary: "" }, A2: { title: "x", summary: "" } }), 0.3, 5)).toEqual(["A1"]);
  });
  it("strips hub entities at run scale so they cannot fuse unrelated stories", () => {
    const o: Record<string, Art> = { A0: { title: "Iran nuclear deal with Trump", summary: "Iran" } };
    for (let i = 1; i <= 30; i++) o[`F${i}`] = { title: "Trump speech in Washington", summary: "Trump politics" };
    o["R1"] = { title: "Iran Hormuz shipping under Trump", summary: "Iran" };
    o["R2"] = { title: "Iran oil exports and Trump", summary: "Iran" };
    expect(new Set(expandNeighbourhood(["A0"], arts(o), 0.2, 20))).toEqual(new Set(["A0", "R1", "R2"]));
  });
  it("signs entities from the title and the summary's first 400 code points, less stopwords", () => {
    expect(articleSignature({ title: "The New York Times-owned Wirecutter", summary: `${"x".repeat(399)} Zanzibar` })).toEqual(new Set(["york", "times", "owned", "wirecutter"]));
  });
});

describe("answerFor", () => {
  it("accepts exactly n verdicts with ids 1..n, in claim order", () => {
    expect(answerFor({ verdicts: [{ id: 2, supported: false }, { id: 1, supported: true }] }, 2)).toEqual({ supported: [true, false] });
  });
  it.each([
    [[{ id: 1, supported: true }], "[2]"],
    [[{ id: 1, supported: true }, { id: 1, supported: true }], "[2]"],
    [[{ id: 1, supported: true }, { id: 3, supported: true }], "[2]"],
    [[{ id: 1, supported: true }, { id: 2, supported: true }, { id: 3, supported: true }], "none"],
  ])("refuses %j", (verdicts, missing) => {
    const out = answerFor({ verdicts }, 2);
    expect("problem" in out && out.problem).toContain(`claim(s) ${missing}`);
  });
});

it("synthesisPrompt says so when the thread has no memory yet", () => {
  expect(synthesisPrompt([], [], ["A1"], arts({ A1: { title: "T", summary: "S" } }))).toBe("RECENT UPDATES:\n(nothing yet -- this is the thread's first tracked day)\nOPEN QUESTIONS:\n(none yet)\n\nTODAY'S SOURCE ARTICLES:\nA1: T\n   S");
});
