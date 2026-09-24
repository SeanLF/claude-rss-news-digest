import { serve } from "@hono/node-server";
import { Resend } from "resend";
import { dbUrl, openDb } from "../store/db.js";
import { siteApp } from "./app.js";
import { AskState, askConfig } from "./ask.js";
import { loadAssets } from "./assets.js";
import { ConfigError, siteConfig, subscriptionsEnabled } from "./config.js";
import { loadCatalogue } from "./sources.js";
import { siteStore } from "./store.js";

// The web tier's process: `node dist/site/main.js`, in its own container, reading the product database
// at DIGEST_DATABASE_URL. It writes nothing there.

let cfg;
try {
  cfg = siteConfig(process.env);
} catch (e) {
  if (e instanceof ConfigError) {
    console.error(JSON.stringify({ site: "config", error: e.message }));
    process.exit(1);
  }
  throw e;
}
const app = siteApp({
  cfg,
  assets: loadAssets(cfg.designDir),
  catalogue: loadCatalogue(cfg.sourcesFile),
  data: siteStore(openDb(dbUrl())),
  // The destination siteConfig checked, passed explicitly: the SDK's own default is real Resend.
  mail: subscriptionsEnabled(cfg) ? new Resend(cfg.resendApiKey, { baseUrl: cfg.resendBaseUrl! }) : undefined,
  ask: new AskState(askConfig(process.env)),
  now: () => new Date(),
});
const port = Number(process.env["PORT"] ?? 8080);
const server = serve({ fetch: app.fetch, port, hostname: "0.0.0.0" }, () => console.log(JSON.stringify({ site: "listening", port })));
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
    // Streams (/ask) may hold the server open; the deadline bounds them anyway.
    setTimeout(() => process.exit(0), 10_000).unref();
  });
}
