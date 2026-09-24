// Reader translation (circulation's translate.rs): a redirect to Google's translate.goog proxy in the
// reader's language. Stateless: no cookie, no storage, no IP read.

// Never "en": Google's proxy answers tl=en with a picker-less error page.
const DEFAULT_LANG = "fr";
const MAX_LANG_TAG = 35;
const MAX_PATH = 128;

// URL-safe BCP-47-shaped: ASCII letters, digits and hyphens, bounded.
const isLangTag = (t: string): boolean => t.length > 0 && t.length <= MAX_LANG_TAG && /^[A-Za-z0-9-]+$/.test(t);

// A ?lang= override, when URL-safe and not English (English falls back to detection).
export function validQueryLang(tag: string | undefined): string | undefined {
  if (tag === undefined) return undefined;
  return isLangTag(tag) && (tag.split("-")[0] ?? "").toLowerCase() !== "en" ? tag : undefined;
}

// The first non-English tag the reader lists, verbatim (Google honours fr-CA and zh-TW), else "fr".
export function pickTargetLang(acceptLanguage: string | undefined): string {
  for (const item of (acceptLanguage ?? "").split(",")) {
    const tag = (item.split(";")[0] ?? "").trim();
    if ((tag.split("-")[0] ?? "").toLowerCase() === "en" || !isLangTag(tag)) continue;
    return tag;
  }
  return DEFAULT_LANG;
}

// A same-origin absolute path safe to put in a Location: one leading slash (never //), bounded, and
// only characters every translatable page path uses.
export const validTranslatePath = (p: string | undefined): string | undefined =>
  p !== undefined && p.startsWith("/") && !p.startsWith("//") && p.length <= MAX_PATH && /^[A-Za-z0-9/_-]+$/.test(p) ? p : undefined;

// "news-digest.example.org" -> "news--digest-example-org.translate.goog": hyphens doubled first.
export const proxyHost = (domain: string): string => `${domain.replaceAll("-", "--").replaceAll(".", "-")}.translate.goog`;

// Where to send the reader: the proxy, or the plain page when no domain is configured (local runs have
// no proxy host).
export function proxyTarget(domain: string | undefined, path: string, queryLang: string | undefined, acceptLanguage: string | undefined): string {
  if (!domain) return path;
  const lang = validQueryLang(queryLang) ?? pickTargetLang(acceptLanguage);
  return `https://${proxyHost(domain)}${path}?_x_tr_sl=en&_x_tr_tl=${lang}&_x_tr_hl=${lang}`;
}
