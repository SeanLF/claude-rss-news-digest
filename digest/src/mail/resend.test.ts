import { afterEach, describe, expect, it, vi } from "vitest";
import { MailDestinationError } from "../resend/destination.js";
import { emailSender, resendClient, ResendSendError, type ResendEmails } from "./resend.js";

type Call = [payload: unknown, options: unknown];
const fake = (reply: Awaited<ReturnType<ResendEmails["send"]>>, calls: Call[]): ResendEmails => ({
  send: (payload, options) => {
    calls.push([payload, options]);
    return Promise.resolve(reply);
  },
});
const email = { from: "Alerts <a@example.com>", to: ["ops@example.com"], subject: "s", html: "<p>h</p>" };

// A fetch that never answers until its signal aborts, as a hung connection does.
const hang = () =>
  vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal?.reason as Error))));

// The dev stack's fake; any URL that is not Resend's own will do.
const FAKE = { RESEND_BASE_URL: "http://resend-fake:8025" };

describe("resendClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });
  it("requests the destination resendBaseUrl chose, never the SDK's own default", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ id: "b1", status: "sent" }), { status: 200 }));
    await resendClient("re_test", {}, FAKE).broadcasts.get("b1");
    expect(spy).toHaveBeenCalledWith("http://resend-fake:8025/broadcasts/b1", expect.anything());
  });
  it("will not construct a client for real Resend outside production", () => {
    expect(() => resendClient("re_test", {}, {})).toThrow(MailDestinationError);
  });
  it("bounds every call: a hung request ends at the timeout as a failed reply", async () => {
    hang();
    const t0 = Date.now();
    const r = await resendClient("re_test", { timeoutMs: 50 }, FAKE).broadcasts.get("b1");
    expect(r.error).not.toBeNull();
    expect(Date.now() - t0).toBeLessThan(2000);
  });
  it("a cancelled activity's signal stops the call in flight", async () => {
    hang();
    const ac = new AbortController();
    const p = resendClient("re_test", { timeoutMs: 60_000, signal: () => ac.signal }, FAKE).broadcasts.create({ from: "a@b.c", segmentId: "s", html: "h", subject: "s" });
    ac.abort();
    expect((await p).error).not.toBeNull();
  });
});

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
