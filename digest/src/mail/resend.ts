import { Resend } from "resend";

// The one place the worker talks to Resend. Alerts use `emailSender`; the broadcast path takes the
// client from `resendClient` for the audience API. Both read RESEND_API_KEY, as the Python does.
export const resendClient = (apiKey: string): Resend => new Resend(apiKey);

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
