import { createHash } from "node:crypto";
import type { MiddlewareHandler } from "hono";
import { secureHeaders } from "hono/secure-headers";
import { NO_FLASH_JS } from "./pages/chrome.js";
import { indexJs, proxyTranslateHideScript, threadsJs, toggleJs } from "./pages/blobs.js";

// The security headers seanfloyd.dev sends from the same box (spec §3), on every response. Inline
// <script> and <style> run only by their sha256 hash, so the policy is a function of the body alone;
// style *attributes* stay allowed (the bias bars' widths are inline), which cannot run script.

// The only inline scripts the site writes. A script is hashed only when it is one of these: a stored
// issue is the pipeline's HTML, and a <script> that ever reached it must stay refused.
const SITE_SCRIPTS: ReadonlySet<string> = new Set([NO_FLASH_JS, toggleJs, proxyTranslateHideScript, indexJs, threadsJs]);

const INLINE = /<(script|style)\b([^>]*)>([\s\S]*?)<\/\1\s*>/gi;
// A browser hashes the element's text after its input stream turned CR and CRLF into LF.
const sha256 = (body: string) => `'sha256-${createHash("sha256").update(body.replaceAll(/\r\n?/g, "\n"), "utf8").digest("base64")}'`;

// The Content-Security-Policy for a response whose body is `html` (empty for a body that is not HTML).
export function contentSecurityPolicy(html: string): string {
  const scripts = new Set<string>();
  const styles = new Set<string>();
  for (const [, tag, attrs, body] of html.matchAll(INLINE)) {
    if (tag!.toLowerCase() === "style") styles.add(sha256(body!));
    else if (!/\bsrc\s*=/i.test(attrs!) && SITE_SCRIPTS.has(body!)) scripts.add(sha256(body!));
  }
  return [
    "default-src 'self'",
    `script-src ${scripts.size ? [...scripts].join(" ") : "'none'"}`,
    ["style-src 'self'", ...styles].join(" "),
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join("; ");
}

// Reads an HTML body once the handler has built it, and sets the policy from what it holds.
const csp = (): MiddlewareHandler => async (c, next) => {
  await next();
  const html = c.res.headers.get("content-type")?.startsWith("text/html") && c.res.body ? await c.res.clone().text() : "";
  c.res.headers.set("content-security-policy", contentSecurityPolicy(html));
};

const headers = (): MiddlewareHandler =>
  secureHeaders({
    strictTransportSecurity: "max-age=31536000; includeSubDomains; preload",
    xFrameOptions: "SAMEORIGIN",
    referrerPolicy: "strict-origin-when-cross-origin",
    xContentTypeOptions: "nosniff",
    permissionsPolicy: { accelerometer: [], camera: [], geolocation: [], gyroscope: [], magnetometer: [], microphone: [], payment: [], usb: [] },
    // Social previews and other sites embed the og:image, so resources stay embeddable cross-origin.
    crossOriginResourcePolicy: false,
    crossOriginEmbedderPolicy: false,
    crossOriginOpenerPolicy: "same-origin",
  });

export const securityHeaders = (): MiddlewareHandler[] => [csp(), headers()];
