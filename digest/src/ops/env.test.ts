import { describe, expect, it } from "vitest";
import { operationsEnvWarning } from "./env.js";

const FULL = { HEALTH_ALERT_EMAIL: "ops@x", RESEND_API_KEY: "re", RESEND_FROM: "d@x", HEALTHCHECK_PING_URL: "https://hc-ping.com/u", BROADCAST_ENABLED: "true", RESEND_AUDIENCE_ID: "aud", RESEND_LIVE: "true" };

describe("operationsEnvWarning", () => {
  it("is silent when alerts, pings and the send are all configured", () => {
    expect(operationsEnvWarning(FULL)).toBeNull();
  });
  it("names every missing variable in one line, grouped by what it silences", () => {
    expect(operationsEnvWarning({})).toBe(
      "operations misconfigured: alerts will not be delivered (HEALTH_ALERT_EMAIL, RESEND_API_KEY, RESEND_FROM unset); the dead-man's switch will not be pinged (HEALTHCHECK_PING_URL unset); nothing will be broadcast (BROADCAST_ENABLED is not true)",
    );
  });
  it("names a mail destination the client will refuse, so a dev worker says why nothing was mailed", () => {
    const { RESEND_LIVE: _, ...dev } = FULL;
    expect(operationsEnvWarning(dev)).toMatch(/^operations misconfigured: every email will be refused \(RESEND_BASE_URL is unset and RESEND_LIVE is not true/);
    expect(operationsEnvWarning({ ...dev, RESEND_BASE_URL: "http://resend-fake:8025" })).toBeNull();
  });
  it("with the send on, a missing audience is named", () => {
    const { RESEND_AUDIENCE_ID: _, ...env } = FULL;
    expect(operationsEnvWarning(env)).toBe("operations misconfigured: the send will fail (RESEND_AUDIENCE_ID unset)");
  });
});
