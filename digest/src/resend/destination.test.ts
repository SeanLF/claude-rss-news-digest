import { describe, expect, it } from "vitest";
import { MailDestinationError, RESEND_API_URL, resendBaseUrl } from "./destination.js";

describe("resendBaseUrl", () => {
  it("reaches real Resend only when the environment says it is production", () => {
    expect(resendBaseUrl({ RESEND_LIVE: "true" })).toBe(RESEND_API_URL);
  });
  it("sends anywhere else to the fake RESEND_BASE_URL names", () => {
    expect(resendBaseUrl({ RESEND_BASE_URL: "http://resend-fake:8025/" })).toBe("http://resend-fake:8025");
  });
  it.each([
    ["no destination at all, whatever the key", { RESEND_API_KEY: "re_real_looking" }, /RESEND_LIVE/],
    ["an empty base URL", { RESEND_BASE_URL: " " }, /RESEND_LIVE/],
    ["Resend itself named as the base URL", { RESEND_BASE_URL: "https://api.resend.com" }, /api\.resend\.com/],
    ["a Resend subdomain", { RESEND_BASE_URL: "https://eu.api.resend.com/" }, /resend\.com/],
    ["a base URL that is not a URL", { RESEND_BASE_URL: "resend-fake:8025" }, /not a URL/],
    ["RESEND_LIVE spelled any other way", { RESEND_LIVE: "1" }, /RESEND_LIVE/],
  ])("refuses %s outside production", (_what, env, message) => {
    expect(() => resendBaseUrl(env)).toThrow(MailDestinationError);
    expect(() => resendBaseUrl(env)).toThrow(message);
  });
  it("refuses production pointed at a fake: every send would vanish while reporting success", () => {
    expect(() => resendBaseUrl({ RESEND_LIVE: "true", RESEND_BASE_URL: "http://resend-fake:8025" })).toThrow(/RESEND_LIVE.*RESEND_BASE_URL/);
  });
});
