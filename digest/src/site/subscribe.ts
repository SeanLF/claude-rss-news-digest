import { createHmac, timingSafeEqual } from "node:crypto";
import mailchecker from "mailchecker";
import type { Resend } from "resend";
import type { SiteConfig } from "./config.js";

// Subscribe and double opt-in confirm (circulation's handlers.rs). The subscriber list lives in Resend;
// nothing about a subscriber is stored here, and no address is ever written to the log.

export const CONFIRM_TTL_S = 48 * 60 * 60;

// Offline syntax and disposable-domain checks (mailchecker's list): hygiene that trims junk without
// sending an address anywhere. Double opt-in is the real abuse control.
export const isValidEmail = (email: string): boolean => mailchecker.isValid(email.trim());

const b64 = (b: Buffer | string): string => Buffer.from(b).toString("base64url");
const sign = (secret: string, payload: string): Buffer => createHmac("sha256", secret).update(payload).digest();

// A stateless confirmation token, `b64url(email "\n" exp).b64url(hmac)`: the token is the pending
// record. The Rust server's format, so a link it mailed still confirms.
export const makeToken = (secret: string, email: string, expUnix: number): string => {
  const payload = `${email}\n${expUnix}`;
  return `${b64(payload)}.${b64(sign(secret, payload))}`;
};

// The email a valid, unexpired token was issued for; undefined for anything tampered, expired or
// malformed. The signature is compared in constant time.
export function verifyToken(secret: string, token: string, nowUnix: number): string | undefined {
  const [p, s, extra] = token.split(".");
  if (p === undefined || s === undefined || extra !== undefined) return undefined;
  if (!/^[A-Za-z0-9_-]*$/.test(p) || !/^[A-Za-z0-9_-]*$/.test(s)) return undefined;
  const payload = Buffer.from(p, "base64url");
  const sig = Buffer.from(s, "base64url");
  const want = createHmac("sha256", secret).update(payload).digest();
  if (sig.length !== want.length || !timingSafeEqual(sig, want)) return undefined;
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(payload);
  } catch {
    return undefined;
  }
  const nl = text.indexOf("\n");
  if (nl < 0) return undefined;
  const exp = text.slice(nl + 1);
  if (!/^\d+$/.test(exp) || Number(exp) <= nowUnix) return undefined;
  return text.slice(0, nl);
}

export const confirmationHtml = (digestName: string, confirmUrl: string): string => `<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:0 auto;padding:32px 24px;color:#1a1a1a;">
  <h1 style="font-size:20px;margin:0 0 12px;">Confirm your subscription</h1>
  <p style="font-size:15px;line-height:1.5;margin:0 0 24px;color:#444;">Tap the button to confirm you want the ${digestName} morning briefing in your inbox.</p>
  <p style="margin:0 0 24px;"><a href="${confirmUrl}" style="display:inline-block;background:#c45a3b;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;font-size:15px;font-weight:600;">Confirm subscription</a></p>
  <p style="font-size:13px;line-height:1.5;color:#888;margin:0;">If you didn't request this, just ignore this email — you won't be subscribed.</p>
</div>`;

// The text/plain part: multipart mail is a deliverability signal, and it is what text clients show.
export const confirmationText = (digestName: string, confirmUrl: string): string =>
  `Confirm your subscription\n\nConfirm you want the ${digestName} morning briefing in your inbox by opening this link:\n${confirmUrl}\n\nIf you didn't request this, just ignore this email — you won't be subscribed.\n`;

// The slice of the Resend SDK the site uses, so tests can stand in for it.
export type Mail = { contacts: Pick<Resend["contacts"], "create">; emails: Pick<Resend["emails"], "send"> };

// Errors are logged by kind and status only: the SDK's message can echo the address.
async function checked(what: string, call: Promise<{ error: { name: string; statusCode: number | null } | null }>): Promise<boolean> {
  try {
    const { error } = await call;
    if (error) {
      console.error(JSON.stringify({ site: "subscribe", error: `Resend ${what} failed`, kind: error.name, status: error.statusCode }));
      return false;
    }
    return true;
  } catch (e) {
    console.error(JSON.stringify({ site: "subscribe", error: `Resend ${what} request failed`, kind: e instanceof Error ? e.name : "unknown" }));
    return false;
  }
}

// Resend upserts, so re-confirming a still-valid link is idempotent. The audience endpoint is the one
// the Rust server used in production; the SDK marks it deprecated in favour of segments.
export const addContact = (cfg: SiteConfig, mail: Mail, email: string): Promise<boolean> =>
  checked("contacts", mail.contacts.create({ audienceId: cfg.resendAudienceId!, email }));

export function sendConfirmation(cfg: SiteConfig, mail: Mail, email: string, confirmUrl: string): Promise<boolean> {
  return checked(
    "email",
    mail.emails.send({
      from: `${cfg.digestName} <${cfg.fromEmail!}>`,
      to: [email],
      subject: `Confirm your subscription to ${cfg.digestName}`,
      html: confirmationHtml(cfg.digestName, confirmUrl),
      text: confirmationText(cfg.digestName, confirmUrl),
      // Replies reach the monitored contact inbox, not a possibly send-only From.
      ...(cfg.contactEmail ? { replyTo: cfg.contactEmail } : {}),
    }),
  );
}
