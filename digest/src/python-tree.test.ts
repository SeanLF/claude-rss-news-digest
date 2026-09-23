import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// After the cut-over the TypeScript pipeline still builds from the Python tree: the worker images copy
// newsroom files. A cleanup that deletes newsroom/ breaks those builds, so each copied file must exist
// and be named in the runbook's list of what still depends on it.
const ROOT = new URL("../../", import.meta.url).pathname;
const DOCKERFILES = ["digest/Dockerfile", "digest/Dockerfile.ci", "digest/python/Dockerfile"];
const RUNBOOK = "docs/2026-09-23-temporal-cutover-runbook.md";

// The repo paths under newsroom/ or migrations/ that a Dockerfile's COPY lines read (build context: the repo root).
export function pythonTreeSources(dockerfile: string): string[] {
  return dockerfile
    .split("\n")
    .filter((l) => /^COPY\s/.test(l) && !/--from=/.test(l))
    .flatMap((l) => l.trim().split(/\s+/).slice(1, -1).filter((a) => !a.startsWith("--")))
    .filter((p) => p.startsWith("newsroom/") || p.startsWith("migrations"));
}

describe("the Python tree the TypeScript images copy", () => {
  it("reads COPY sources, not destinations or other stages", () => {
    const text = "FROM x\nCOPY --from=ghcr.io/uv /uv /bin/\nCOPY --chown=a:a newsroom/src/a.py newsroom/src/b.py /app/src/\nCOPY digest/x.ts /app/\nRUN cp newsroom/c.py /tmp";
    expect(pythonTreeSources(text)).toEqual(["newsroom/src/a.py", "newsroom/src/b.py"]);
  });

  // Negative control: a copied path that is gone is caught.
  it("flags a copied path that no longer exists", () => {
    const missing = pythonTreeSources("COPY newsroom/src/deleted_module.py /app/src/").filter((p) => !existsSync(join(ROOT, p)));
    expect(missing).toEqual(["newsroom/src/deleted_module.py"]);
  });

  const copied = DOCKERFILES.flatMap((f) => pythonTreeSources(readFileSync(join(ROOT, f), "utf8")));

  it("copies the worker's Python modules", () => {
    expect(copied).toEqual(expect.arrayContaining(["newsroom/src/fulltext.py", "newsroom/src/gnews.py", "newsroom/src/config.py"]));
  });

  it.each(DOCKERFILES)("%s copies only files that exist", (f) => {
    expect(pythonTreeSources(readFileSync(join(ROOT, f), "utf8")).filter((p) => !existsSync(join(ROOT, p)))).toEqual([]);
  });

  it("the runbook names every copied file", () => {
    const runbook = readFileSync(join(ROOT, RUNBOOK), "utf8");
    expect([...new Set(copied)].filter((p) => !runbook.includes(`\`${p}\``))).toEqual([]);
  });
});
