import { describe, expect, it } from "vitest";
import { version } from "./index.js";

describe("scaffold", () => {
  it("exports a version", () => {
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
