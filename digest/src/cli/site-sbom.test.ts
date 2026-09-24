import { describe, expect, it } from "vitest";
import { siteSbom } from "./site-sbom.js";

const LOCK = {
  name: "digest",
  version: "0.1.0",
  lockfileVersion: 3,
  packages: {
    "": { name: "digest", version: "0.1.0" },
    "node_modules/hono": { version: "4.13.8", resolved: "https://registry.npmjs.org/hono/-/hono-4.13.8.tgz", integrity: "sha512-AAEC", license: "MIT" },
    "node_modules/@hono/node-server": { version: "2.1.1", integrity: "sha512-AwQF", license: "MIT" },
    "node_modules/pg": { version: "8.23.0", license: "MIT" },
    "node_modules/pg/node_modules/pg-types": { version: "2.2.0", license: "MIT" },
    "node_modules/pg-types": { version: "4.0.0", license: "MIT" },
    "node_modules/vitest": { version: "5.0.1", dev: true, license: "MIT" },
  },
};
const meta = (...inputs: string[]) => ({ inputs: Object.fromEntries(inputs.map((i) => [i, { bytes: 1, imports: [] }])), outputs: {} });

describe("site-sbom", () => {
  it("lists exactly the packages the bundle contains, as npm purls with the lockfile's versions", () => {
    const bom = siteSbom(LOCK, meta("src/site/main.ts", "node_modules/hono/dist/index.js", "node_modules/hono/dist/router.js", "node_modules/@hono/node-server/dist/index.mjs", "node_modules/pg/lib/index.js", "node_modules/pg/node_modules/pg-types/index.js"));
    expect(bom.bomFormat).toBe("CycloneDX");
    expect(bom.components.map((c) => c.purl)).toEqual(["pkg:npm/%40hono/node-server@2.1.1", "pkg:npm/hono@4.13.8", "pkg:npm/pg-types@2.2.0", "pkg:npm/pg@8.23.0"]);
    const nodeServer = bom.components[0]!;
    expect(nodeServer).toMatchObject({ type: "library", group: "@hono", name: "node-server", version: "2.1.1", "bom-ref": "pkg:npm/%40hono/node-server@2.1.1", licenses: [{ license: { id: "MIT" } }] });
    expect(nodeServer.hashes).toEqual([{ alg: "SHA-512", content: "030405" }]);
  });

  it("never lists a package the bundle does not contain, dev or not", () => {
    const bom = siteSbom(LOCK, meta("node_modules/hono/dist/index.js"));
    expect(bom.components.map((c) => c.name)).toEqual(["hono"]);
  });

  it("refuses a bundled package the lockfile does not know: an SBOM that drops it would read clean", () => {
    expect(() => siteSbom(LOCK, meta("node_modules/left-pad/index.js"))).toThrow(/left-pad/);
  });

  it("refuses a metafile with no packages at all: an empty SBOM audits clean", () => {
    expect(() => siteSbom(LOCK, meta("src/site/main.ts"))).toThrow(/no packages/);
  });
});
