import { MailDestinationError, resendBaseUrl } from "../resend/destination.js";

// The site's configuration from its environment. Empty means unset: compose and terraform forward
// optional variables as "".

export interface SiteConfig {
  digestName: string;
  digestDomain: string | undefined;
  homepageUrl: string | undefined;
  sourceUrl: string | undefined;
  // Subscriptions are on when both are set; the index then shows the subscribe band.
  resendApiKey: string | undefined;
  resendAudienceId: string | undefined;
  // Where Resend requests go (mail/resend.ts resendBaseUrl); set whenever subscriptions are on.
  resendBaseUrl: string | undefined;
  // The From on confirmation mail; may be a send-only address, so never the reply target.
  fromEmail: string | undefined;
  // Where readers' replies and the web mailto go: CONTACT_EMAIL, else RESEND_FROM.
  contactEmail: string | undefined;
  // Signs double opt-in confirmation links. Required whenever subscriptions and double opt-in are on.
  subscribeTokenSecret: string | undefined;
  // SUBSCRIBE_DOUBLE_OPT_IN, default on; "false", "0" or "no" is the rollback lever to direct add.
  doubleOptIn: boolean;
  sourcesFile: string;
  designDir: string;
}

export class ConfigError extends Error {
  override name = "ConfigError";
}

const set = (v: string | undefined): string | undefined => (v === undefined || v.trim() === "" ? undefined : v.trim());

export function siteConfig(env: NodeJS.ProcessEnv): SiteConfig {
  const fromEmail = set(env["RESEND_FROM"]);
  const cfg: SiteConfig = {
    digestName: set(env["DIGEST_NAME"]) ?? "News Digest",
    digestDomain: set(env["DIGEST_DOMAIN"]),
    homepageUrl: set(env["HOMEPAGE_URL"]),
    sourceUrl: set(env["SOURCE_URL"]),
    resendApiKey: set(env["RESEND_API_KEY"]),
    resendAudienceId: set(env["RESEND_AUDIENCE_ID"]),
    resendBaseUrl: undefined,
    fromEmail,
    contactEmail: set(env["CONTACT_EMAIL"]) ?? fromEmail,
    subscribeTokenSecret: set(env["SUBSCRIBE_TOKEN_SECRET"]),
    doubleOptIn: !["false", "0", "no"].includes((env["SUBSCRIBE_DOUBLE_OPT_IN"] ?? "").trim().toLowerCase()),
    sourcesFile: set(env["SOURCES_FILE"]) ?? "/app/sources.json",
    designDir: set(env["DESIGN_DIR"]) ?? "/app/design",
  };
  if (subscriptionsEnabled(cfg)) {
    try {
      cfg.resendBaseUrl = resendBaseUrl(env);
    } catch (e) {
      if (e instanceof MailDestinationError) throw new ConfigError(`subscriptions are on, but ${e.message}`);
      throw e;
    }
  }
  // The Rust server warned once and then added every signup to the audience unconfirmed. A
  // subscription path that silently skips its consent step is worse than none, so it refuses to start.
  if (subscriptionsEnabled(cfg) && cfg.doubleOptIn) {
    const missing = [
      ...(cfg.subscribeTokenSecret ? [] : ["SUBSCRIBE_TOKEN_SECRET (signs the confirmation link)"]),
      ...(cfg.digestDomain ? [] : ["DIGEST_DOMAIN (the confirmation link must be absolute)"]),
      ...(cfg.fromEmail ? [] : ["RESEND_FROM (the confirmation email's sender)"]),
    ];
    if (missing.length) {
      throw new ConfigError(`subscriptions are on with double opt-in, but ${missing.join(", ")} is unset; set it, or set SUBSCRIBE_DOUBLE_OPT_IN=false to add contacts without confirmation on purpose`);
    }
  }
  return cfg;
}

export const subscriptionsEnabled = (c: SiteConfig): boolean => c.resendApiKey !== undefined && c.resendAudienceId !== undefined;

// "https://<domain>", or "" when no domain is configured (links then stay root-relative).
export const baseUrl = (c: SiteConfig): string => (c.digestDomain ? `https://${c.digestDomain}` : "");

export const privacyUrl = (c: SiteConfig): string => (c.homepageUrl ? `${c.homepageUrl.replace(/\/+$/, "")}/privacy` : "/privacy");

export const ogImageUrl = (c: SiteConfig): string => (c.digestDomain ? `https://${c.digestDomain}/og-image.png` : "");

// A root-relative path made absolute when a domain is configured.
export const link = (c: SiteConfig, path: string): string => `${baseUrl(c)}${path}`;
