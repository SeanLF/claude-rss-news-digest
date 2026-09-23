import { describe, expect, it } from "vitest";
import { emailSender, ResendSendError, type ResendEmails } from "./resend.js";

type Call = [payload: unknown, options: unknown];
const fake = (reply: Awaited<ReturnType<ResendEmails["send"]>>, calls: Call[]): ResendEmails => ({
  send: (payload, options) => {
    calls.push([payload, options]);
    return Promise.resolve(reply);
  },
});
const email = { from: "Alerts <a@example.com>", to: ["ops@example.com"], subject: "s", html: "<p>h</p>" };

describe("emailSender", () => {
  it("sends through the SDK and returns the email id, passing the idempotency key as the SDK's option", async () => {
    const calls: Call[] = [];
    const send = emailSender(fake({ data: { id: "em_1" }, error: null, headers: null }, calls));
    expect(await send(email, { idempotencyKey: "k-1" })).toEqual({ id: "em_1" });
    expect(calls).toEqual([[email, { idempotencyKey: "k-1" }]]);
  });
  it("turns the SDK's error result into a thrown error, since the SDK itself never throws", async () => {
    const send = emailSender(fake({ data: null, error: { name: "rate_limit_exceeded", message: "slow down", statusCode: 429 }, headers: null }, []));
    const err = await send(email).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ResendSendError);
    expect(err).toMatchObject({ message: "rate_limit_exceeded (429): slow down", statusCode: 429 });
  });
});
