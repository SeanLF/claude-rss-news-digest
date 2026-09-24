import { existsSync } from "node:fs";
import { siteApp, type SiteDeps } from "./app.js";
import { AskState } from "./ask.js";
import { loadAssets } from "./assets.js";
import { type SiteConfig, siteConfig } from "./config.js";
import type { SiteData } from "./data.js";
import { loadCatalogue } from "./sources.js";

// Test wiring: the real assets, catalogue and config parser, with the data and the clock the test gives.

// The ci-ts image copies design/ and sources.json to /app; a checkout has them beside digest/.
const firstOf = (...paths: string[]): string => paths.find((p) => existsSync(p)) ?? paths[0]!;
export const DESIGN_DIR = firstOf("/app/design", new URL("../../../design", import.meta.url).pathname);
export const SOURCES_FILE = firstOf("/app/sources.json", new URL("../../../newsroom/sources.json", import.meta.url).pathname);

export const testConfig = (env: Record<string, string> = {}): SiteConfig => siteConfig({ DIGEST_NAME: "News Digest", DIGEST_DOMAIN: "digest.example", DESIGN_DIR, SOURCES_FILE, ...env });

export function testApp(data: SiteData, over: Partial<SiteDeps> = {}) {
  const cfg = over.cfg ?? testConfig();
  return siteApp({
    cfg,
    assets: loadAssets(cfg.designDir),
    catalogue: loadCatalogue(cfg.sourcesFile),
    data,
    mail: undefined,
    ask: new AskState(undefined),
    now: () => new Date("2026-09-23T12:00:00Z"),
    ...over,
  });
}

// A SiteData from fixed rows, for route tests that are not about SQL.
const none = async (): Promise<undefined> => undefined;
export function fakeData(over: Partial<SiteData> = {}): SiteData {
  return {
    indexMeta: async () => ({ total: 0, firstDate: null, newestDate: null, totalStories: 0 }),
    archive: async () => [],
    issue: none,
    latestIssueDate: none,
    feed: async () => [],
    search: async () => [],
    threadIndex: async () => ({ ongoing: [], older: [], olderTotal: 0 }),
    mergedInto: none,
    thread: none,
    stats: async () => ({ sourceHealth: [], sourceUsage: [], recentRuns: [], dedup: { count: 0, avg: null, min: null, max: null }, neverSelected: [], cost: { runs: 0, keptTotal: 0, costTotal: 0, shippedTotal: 0, recipientsLatest: 0 } }),
    ping: async () => undefined,
    ...over,
  };
}
