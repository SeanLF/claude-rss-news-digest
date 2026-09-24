import { describe, expect, it } from "vitest";
import { ConfigError, siteConfig } from "./config.js";

// The opt-in misconfiguration the Rust server warned about and then worked around by adding every
// signup unconfirmed is a refusal to start (spec §3).
const SUBS = { RESEND_API_KEY: "re_x", RESEND_AUDIENCE_ID: "aud", RESEND_FROM: "d@send.example", DIGEST_DOMAIN: "digest.example", SUBSCRIBE_TOKEN_SECRET: "sixteen-chars-ok", RESEND_BASE_URL: "http://resend-fake:8025" };

describe("siteConfig", () => {
  it("refuses to start with subscriptions on and real Resend reachable outside production", () => {
    const { RESEND_BASE_URL: _, ...dev } = SUBS;
    expect(() => siteConfig(dev)).toThrow(ConfigError);
    expect(() => siteConfig(dev)).toThrow(/RESEND_LIVE/);
    expect(siteConfig({ ...dev, RESEND_LIVE: "true" }).resendBaseUrl).toBe("https://api.resend.com");
    expect(siteConfig(SUBS).resendBaseUrl).toBe("http://resend-fake:8025");
  });

  it("starts with subscriptions and double opt-in fully configured", () => {
    expect(siteConfig(SUBS)).toMatchObject({ doubleOptIn: true, subscribeTokenSecret: "sixteen-chars-ok", contactEmail: "d@send.example" });
  });

  it.each([
    ["SUBSCRIBE_TOKEN_SECRET", /SUBSCRIBE_TOKEN_SECRET/],
    ["DIGEST_DOMAIN", /DIGEST_DOMAIN/],
    ["RESEND_FROM", /RESEND_FROM/],
  ])("refuses to start without %s", (key, message) => {
    expect(() => siteConfig({ ...SUBS, [key]: "" })).toThrow(ConfigError);
    expect(() => siteConfig({ ...SUBS, [key]: "" })).toThrow(message);
  });

  it("starts without a secret when double opt-in is switched off on purpose", () => {
    expect(siteConfig({ ...SUBS, SUBSCRIBE_TOKEN_SECRET: "", SUBSCRIBE_DOUBLE_OPT_IN: "false" }).doubleOptIn).toBe(false);
  });

  it("starts without any of it when subscriptions are off", () => {
    expect(siteConfig({ DIGEST_DOMAIN: "digest.example" })).toMatchObject({ resendApiKey: undefined, digestName: "News Digest" });
  });

  it("treats an empty value as unset, and falls back from CONTACT_EMAIL to RESEND_FROM", () => {
    expect(siteConfig({ CONTACT_EMAIL: "", RESEND_FROM: "from@example" }).contactEmail).toBe("from@example");
    expect(siteConfig({ CONTACT_EMAIL: "hi@example", RESEND_FROM: "from@example" }).contactEmail).toBe("hi@example");
  });
});
