import { serve } from "@hono/node-server";
import { resendFake } from "./fake.js";

// `node dist/devmail/main.js`: the dev stack's resend-fake (docker-compose.yml). Dev only.
const port = Number(process.env["PORT"] ?? 8025);
const server = serve({ fetch: resendFake().app.fetch, port, hostname: "0.0.0.0" }, () => console.log(JSON.stringify({ devmail: "listening", port })));
for (const signal of ["SIGTERM", "SIGINT"] as const) process.on(signal, () => server.close(() => process.exit(0)));
