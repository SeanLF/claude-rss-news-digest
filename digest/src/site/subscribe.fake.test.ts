import type { AddressInfo } from "node:net";
import { serve, type ServerType } from "@hono/node-server";
import { Resend } from "resend";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { resendFake, type ResendFake } from "../devmail/fake.js";
import { siteConfig } from "./config.js";
import { addContact, sendConfirmation } from "./subscribe.js";

// Subscribe and confirm against the dev stack's fake, through the client main.ts builds: the
// confirmation lands in the fake and the confirmed reader is a contact the broadcast then counts.
let fake: ResendFake;
let server: ServerType;
let base: string;
beforeAll(async () => {
  fake = resendFake();
  server = serve({ fetch: fake.app.fetch, port: 0, hostname: "127.0.0.1" });
  await new Promise((r) => server.once("listening", r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => {
  server.close();
});

describe("subscribe against resend-fake", () => {
  it("mails the confirmation there, then adds the contact to the audience, idempotently", async () => {
    const cfg = siteConfig({ RESEND_API_KEY: "re_dev_fake", RESEND_AUDIENCE_ID: "aud-dev", RESEND_FROM: "news@dev.invalid", DIGEST_DOMAIN: "site.dev.invalid", SUBSCRIBE_TOKEN_SECRET: "sixteen-chars-ok", RESEND_BASE_URL: base });
    const mail = new Resend(cfg.resendApiKey, { baseUrl: cfg.resendBaseUrl! });
    expect(await sendConfirmation(cfg, mail, "reader@example.com", "https://site.dev.invalid/confirm?token=t")).toBe(true);
    expect(await addContact(cfg, mail, "reader@example.com")).toBe(true);
    expect(await addContact(cfg, mail, "reader@example.com")).toBe(true); // Resend upserts
    expect(fake.messages()).toMatchObject([{ kind: "email", to: ["reader@example.com"], subject: "Confirm your subscription to News Digest", replyTo: ["news@dev.invalid"] }]);
    expect(fake.contacts("aud-dev").map((c) => [c.email, c.unsubscribed])).toEqual([["reader@example.com", false]]);
  });
});
