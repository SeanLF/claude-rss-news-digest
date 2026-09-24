import type { Context, MiddlewareHandler } from "hono";
import { NONCE, type SecureHeadersVariables, secureHeaders } from "hono/secure-headers";

// The security headers seanfloyd.dev sends from the same box (spec §3), on every response. Inline
// <script> and <style> run only with this response's nonce; style *attributes* stay allowed (the bias
// bars' widths are inline), which cannot run script.
export const securityHeaders = (): MiddlewareHandler =>
  secureHeaders({
    contentSecurityPolicy: {
      defaultSrc: ["'self'"],
      scriptSrc: [NONCE],
      styleSrc: ["'self'", NONCE],
      styleSrcAttr: ["'unsafe-inline'"],
      imgSrc: ["'self'", "data:"],
      fontSrc: ["'self'"],
      connectSrc: ["'self'"],
      objectSrc: ["'none'"],
      baseUri: ["'none'"],
      formAction: ["'self'"],
      frameAncestors: ["'self'"],
    },
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

// This response's CSP nonce, minted in siteApp before the middleware above reads it.
export function nonceOf(c: Context<{ Variables: SecureHeadersVariables }>): string {
  const n = c.get("secureHeadersNonce");
  if (!n) throw new Error("no CSP nonce: the nonce middleware must run before the page handlers");
  return n;
}
