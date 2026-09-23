import { describe, expect, it } from "vitest";
import { cleanQuestions, deltaFromFacts, slugify, whatsNew } from "./text.js";

// Cases carried from newsroom/tests/test_threads.py.
describe("deltaFromFacts", () => {
  it("skips a fact with a bare self-citation and promotes the next", () => {
    const facts = [
      { fact: "600 of 1,500 evacuees returned as the fire nears control, according to A238.", sources: ["A238"] },
      { fact: "Spanish authorities suspect arson.", sources: ["A254"] },
      { fact: "Third fact.", sources: ["A243"] },
      { fact: "Fourth fact.", sources: ["A9"] },
    ];
    expect(deltaFromFacts(facts)).toBe("Spanish authorities suspect arson. Third fact. Fourth fact.");
  });
  it("keeps a designator the fact does not cite", () => {
    expect(deltaFromFacts([{ fact: "The iPhone 17e ships with the A19 chip.", sources: ["A404"] }])).toBe("The iPhone 17e ships with the A19 chip.");
  });
  it("strips a delimited citation without dropping the fact", () => {
    expect(deltaFromFacts([{ fact: "Talks resumed in Doha. (A238)", sources: ["A238"] }])).toBe("Talks resumed in Doha.");
  });
  it.each([["A3"], [null], [{ A3: 1 }], [3]])("keeps a clean fact when sources is %j, not a list", (sources) => {
    expect(deltaFromFacts([{ fact: "Talks resumed at 3 p.m. Plan A was rejected.", sources }])).toBe("Talks resumed at 3 p.m. Plan A was rejected.");
  });
  it("survives non-string facts and non-object entries", () => {
    expect(deltaFromFacts([{ fact: "Leak, per A1.", sources: ["A1"] }, { fact: "One." }, "not an object", { fact: 123 }, { fact: "Two." }])).toBe("One. Two.");
  });
  it("detects a self-citation despite a padded source id", () => {
    expect(deltaFromFacts([{ fact: "Fire nears control, according to A238 today.", sources: ["A238 "] }])).toBe("");
  });
  it("treats a letter-adjacent id as part of a word, as Python's Unicode \\b does", () => {
    expect(deltaFromFacts([{ fact: "The éA238 model shipped.", sources: ["A238"] }])).toBe("The éA238 model shipped.");
  });
});

describe("cleanQuestions", () => {
  it("drops a question citing its run's own sources, strips a delimited one, keeps a designator", () => {
    expect(cleanQuestions(["Will the ceasefire hold through winter?", "What did A12 report?"], ["A12"])).toEqual(["Will the ceasefire hold through winter?"]);
    expect(cleanQuestions(["Is the dynamic identified by analysts (A7) deliberate?"], ["A7"])).toEqual(["Is the dynamic identified by analysts deliberate?"]);
    expect(cleanQuestions(["Will the A19 chip ship on time?"], ["A3"])).toEqual(["Will the A19 chip ship on time?"]);
  });
  it("drops blank and non-string entries", () => {
    expect(cleanQuestions(["Real question?", "", null, 7], ["A1"])).toEqual(["Real question?"]);
  });
});

describe("whatsNew and slugify", () => {
  it("reads whats_new, and nothing from missing or corrupt content", () => {
    expect(whatsNew(JSON.stringify({ whats_new: [{ fact: "x" }] }))).toEqual([{ fact: "x" }]);
    expect(whatsNew(null)).toEqual([]);
    expect(whatsNew("{not json")).toEqual([]);
    expect(whatsNew("[1]")).toEqual([]);
  });
  it("slugifies as threads._slugify does", () => {
    expect(slugify("US-Iran talks: round 2!")).toBe("us-iran-talks-round-2");
    expect(slugify("¡¡¡")).toBe("thread");
    expect(slugify("a".repeat(70))).toHaveLength(60);
  });
});
