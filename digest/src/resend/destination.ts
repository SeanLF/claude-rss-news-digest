// Where the Resend SDK sends, for the worker (mail/resend.ts) and the site (site/main.ts) alike. Shared
// by both, so it lives outside the pipeline's mail/ that the site may not import.
export const RESEND_API_URL = "https://api.resend.com";

export class MailDestinationError extends Error {
  override name = "MailDestinationError";
}

// Where every Resend request goes. Real Resend is reachable only from an environment that says it is
// production (RESEND_LIVE=true); anywhere else RESEND_BASE_URL must name a stand-in such as the dev
// stack's resend-fake. Keyed on the destination, not the key: a dev container that inherits a real key
// from .env still mails nobody. Production with a base URL is refused too, since a fake there would
// swallow every send and report success.
export function resendBaseUrl(env: Record<string, string | undefined>): string {
  const base = env["RESEND_BASE_URL"]?.trim() || undefined;
  if (env["RESEND_LIVE"] === "true") {
    if (base) throw new MailDestinationError(`RESEND_LIVE=true but RESEND_BASE_URL is set (${base}): production would mail a stand-in; unset one of them`);
    return RESEND_API_URL;
  }
  if (!base) {
    throw new MailDestinationError(
      "RESEND_BASE_URL is unset and RESEND_LIVE is not true: refusing to reach real Resend; point RESEND_BASE_URL at a fake (the dev stack runs resend-fake), or set RESEND_LIVE=true in production",
    );
  }
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new MailDestinationError(`RESEND_BASE_URL is not a URL: ${base}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new MailDestinationError(`RESEND_BASE_URL is not a URL: ${base}`);
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (host === "resend.com" || host.endsWith(".resend.com")) throw new MailDestinationError(`RESEND_BASE_URL names Resend itself (${host}) and RESEND_LIVE is not true`);
  return base.replace(/\/+$/, "");
}
