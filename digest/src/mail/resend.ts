import { Resend, type Response as ResendReply } from "resend";
import { resendBaseUrl } from "../resend/destination.js";

// The one place the worker talks to Resend. Alerts use `emailSender`; the broadcast path takes the
// client from `resendClient` for the audience API. Both read RESEND_API_KEY, as the Python does.
export const RESEND_TIMEOUT_MS = 30_000;

// The SDK sets no timeout and takes no signal, so a hung request would outlive the activity that made
// it: a cancelled send could still create a broadcast. Every request goes through fetchRequest, the
// SDK's one fetch, so bounding it there bounds them all; an aborted request is a failed reply.
class BoundedResend extends Resend {
  constructor(
    key: string,
    baseUrl: string,
    private readonly bounds: { timeoutMs: number; signal?: () => AbortSignal | undefined },
  ) {
    super(key, { baseUrl });
  }
  override fetchRequest<T>(path: string, options: RequestInit = {}): Promise<ResendReply<T>> {
    const outer = this.bounds.signal?.();
    const signal = AbortSignal.any([AbortSignal.timeout(this.bounds.timeoutMs), ...(outer ? [outer] : [])]);
    return super.fetchRequest<T>(path, { ...options, signal });
  }
}
// Throws MailDestinationError when the environment does not allow the destination (resendBaseUrl).
export const resendClient = (apiKey: string, bounds: { timeoutMs?: number; signal?: () => AbortSignal | undefined } = {}, env: Record<string, string | undefined> = process.env): Resend =>
  new BoundedResend(apiKey, resendBaseUrl(env), { timeoutMs: bounds.timeoutMs ?? RESEND_TIMEOUT_MS, ...(bounds.signal ? { signal: bounds.signal } : {}) });

export interface Email {
  from: string;
  to: string[];
  subject: string;
  html: string;
}
export type SendEmail = (email: Email, opts?: { idempotencyKey?: string }) => Promise<{ id: string }>;
// The slice of the SDK the sender uses, so tests can stand in for it.
export type ResendEmails = Pick<Resend["emails"], "send">;

export class ResendSendError extends Error {
  constructor(
    message: string,
    readonly statusCode: number | null,
  ) {
    super(message);
    this.name = "ResendSendError";
  }
}

// The SDK reports a failed send as `{ error }` and never throws, so an unchecked result is a silent
// drop. This turns it into a throw the caller's retry policy can see.
export function emailSender(emails: ResendEmails): SendEmail {
  return async (email, opts) => {
    const { data, error } = await emails.send(email, opts?.idempotencyKey ? { idempotencyKey: opts.idempotencyKey } : undefined);
    if (error) throw new ResendSendError(`${error.name} (${error.statusCode ?? "no status"}): ${error.message}`, error.statusCode);
    return { id: data.id };
  };
}
