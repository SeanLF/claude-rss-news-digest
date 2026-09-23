import { defineConfig } from "vitest/config";
// The first test in each file starts that worker's PGlite and applies the schema, which took 5.2-6.4 s
// with the host's load average near 15 (2026-09-23), past vitest's 5 s default: eight files failed
// on their first test and passed alone.
export default defineConfig({ test: { include: ["src/**/*.test.ts"], testTimeout: 30_000 } });
