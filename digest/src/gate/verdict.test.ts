import { describe, expect, it } from "vitest";
import { parseVerdicts } from "./verdict.js";

describe("parseVerdicts", () => {
  it("parses the outermost JSON array out of a chatty reply and validates each cell", () => {
    expect(parseVerdicts('thinking...\n[{"story":0,"criterion":1,"pass":true,"reason":"ok"}]\ndone')).toEqual([{ story: 0, criterion: 1, pass: true, reason: "ok" }]);
    expect(() => parseVerdicts("no json here")).toThrow(/array/);
    expect(() => parseVerdicts('[{"story":0,"criterion":9,"pass":true}]')).toThrow(/malformed/);
  });
});
