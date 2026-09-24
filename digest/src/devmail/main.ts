import { serve } from "@hono/node-server";
import { resendFake } from "./fake.js";

// `node dist/devmail/main.js`: the dev stack's resend-fake (docker-compose.yml). Dev only.
const port = Number(process.env["PORT"] ?? 8025);
// RESEND_FAKE_STATE: the file its state is kept in (docker-compose.yml puts it on a volume); unset, memory only.
const stateFile = process.env["RESEND_FAKE_STATE"] || undefined;
const fake = resendFake(stateFile ? { stateFile } : {});
const server = serve({ fetch: fake.app.fetch, port, hostname: "0.0.0.0" }, () => console.log(JSON.stringify({ devmail: "listening", port, state: stateFile ?? "memory" })));
for (const signal of ["SIGTERM", "SIGINT"] as const) process.on(signal, () => server.close(() => process.exit(0)));
