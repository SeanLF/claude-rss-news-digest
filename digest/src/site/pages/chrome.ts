import type { Assets } from "../assets.js";
import { fontFace } from "../assets.js";
import { type SiteConfig, baseUrl, ogImageUrl, privacyUrl, subscriptionsEnabled } from "../config.js";
import { brandHtml } from "../text.js";
import { chromeCss, toggleJs } from "./blobs.js";

// The frame every page but the issue shares (circulation's templates/chrome.rs): the head, the top
// bar, the footer and the theme toggle.

// What every page render needs: the configuration and the assets.
export interface PageCtx {
  cfg: SiteConfig;
  assets: Assets;
}

// An inline script runs only if its body is one security.ts hashes.
export const script = (body: string): string => `<script>${body}</script>`;
export const style = (body: string): string => `<style>${body}</style>`;

export const SKIP_HTML = '<a class="skip" href="#main">Skip to content</a>';
const FAVICON =
  '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E%3Crect width=%2216%22 height=%2216%22 rx=%222%22 fill=%22%23b1352a%22/%3E%3C/svg%3E">';
// Runs before first paint, so a stored light/dark choice shows no flash of the other theme.
export const NO_FLASH_JS = "(function(){try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();";
export const TOGGLE_BTN =
  '<button class="toggle" id="themeBtn" type="button" aria-label="Theme"><span class="tglyphs" aria-hidden="true"><span class="tglyph tg-cur">◐</span><span class="tglyph tg-next">☀</span></span><span class="tword">System</span></button>';
export const toggleScript = script(toggleJs);

const navRow = (items: [string, string][]): string =>
  items.map(([href, label], i) => `<a href="${href}">${label}</a>${i + 1 === items.length ? "" : '<span class="sep" aria-hidden="true">&middot;</span>'}`).join("");

export const translatePill = (href: string): string => `<a class="pill" href="${href}"><span class="g" aria-hidden="true">文A</span> Translate</a>`;

export const topbar = (nav: [string, string][], right: string): string =>
  `<div class="topbar"><nav class="topnav" aria-label="Site navigation">${navRow(nav)}</nav><nav class="topright" aria-label="Reader tools">${right}</nav></div>`;

export const footer = (links: [string, string][], tagline: string): string =>
  `<footer class="site-foot"><div class="row">${navRow(links)}</div><p style="margin:0;">${tagline}</p></footer>`;

export const subMasthead = (brand: string, title: string, kicker: string, stat: string): string =>
  `<header class="masthead"><a class="brandmark" href="/">${brand}</a><h1 class="h1">${title}</h1><div class="sub"><span class="kicker">${kicker}</span><span class="stat">${stat}</span></div></header>`;

export const ogImageTags = (imageUrl: string): string => `<meta property="og:image" content="${imageUrl}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">`;

export const brand = (ctx: PageCtx): string => brandHtml(ctx.cfg.digestName);

// The inlined critical sheet: the @font-face, the shared tokens, the chrome, then the page's own CSS.
export function pageHead(ctx: PageCtx, title: string, description: string, pageCss: string, extraHead = ""): string {
  const canonical = baseUrl(ctx.cfg);
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
${FAVICON}
${script(NO_FLASH_JS)}
<link rel="alternate" type="application/atom+xml" title="${title}" href="${canonical}/feed.xml">
<meta property="og:title" content="${title}">
<meta property="og:description" content="${description}">
<meta property="og:type" content="website">
<meta property="og:url" content="${canonical}">
<meta property="og:site_name" content="${title}">
<meta name="description" content="${description}">
${ogImageTags(ogImageUrl(ctx.cfg))}
${style(`${fontFace(ctx.assets.fontUrl)}${ctx.assets.tokensCss}${chromeCss}${pageCss}`)}${extraHead}
</head>`;
}

function footerLinks(cfg: SiteConfig): [string, string][] {
  const links: [string, string][] = [
    ["/", "Archive"],
    ["/sources", "Sources"],
    ["/threads", "Threads"],
    ["/stats", "Stats"],
    ["/feed.xml", "RSS"],
  ];
  if (cfg.sourceUrl) links.push([cfg.sourceUrl, "GitHub"]);
  links.push([privacyUrl(cfg), "Privacy"]);
  if (cfg.homepageUrl) links.push([cfg.homepageUrl, cfg.homepageUrl.replace(/^https:\/\//, "").replace(/^http:\/\//, "")]);
  return links;
}

// The top bar and footer every page but the index uses. `current` ("sources" | "threads" | "stats",
// or "" to keep all three) is left out of the nav; `translatePath` is this page's own path, so the
// Translate pill translates the page the reader is on.
export function subChrome(cfg: SiteConfig, current: string, translatePath: string, tagline: string): { topbar: string; footer: string } {
  const nav: [string, string][] = [["/", "&larr; Archive"]];
  for (const [href, label, key] of [
    ["/sources", "Sources", "sources"],
    ["/threads", "Threads", "threads"],
    ["/stats", "Stats", "stats"],
  ] as const) {
    if (key !== current) nav.push([href, label]);
  }
  const right = `${subscriptionsEnabled(cfg) ? '<a class="sublink" href="/#subscribe">Subscribe</a>' : ""}${translatePill(`/translate?to=${translatePath}`)}${TOGGLE_BTN}`;
  return { topbar: topbar(nav, right), footer: footer(footerLinks(cfg), tagline) };
}

export function indexChrome(cfg: SiteConfig): { topbar: string; footer: string } {
  const nav: [string, string][] = [
    ["/sources", "Sources"],
    ["/threads", "Threads"],
    ["/stats", "Stats"],
  ];
  const right = `${subscriptionsEnabled(cfg) ? '<a class="sublink" href="#subscribe">Subscribe</a>' : ""}${translatePill("/translate?to=/")}${TOGGLE_BTN}`;
  return {
    topbar: topbar(nav, right),
    footer: footer(footerLinks(cfg), "An automated daily briefing. Curated and fact-checked by Claude; no human edits any issue. &copy; Sean Floyd"),
  };
}

// The frame around a page's <main>: skip link, top bar, footer, and the scripts at the end of <body>.
export function pageBody(ctx: PageCtx, chrome: { topbar: string; footer: string }, inner: string, scripts: string[] = [], bodyAttrs = ""): string {
  return `
<body${bodyAttrs}>
${SKIP_HTML}
<div class="wrap"><div class="col">
    ${chrome.topbar}
    ${inner}
    ${chrome.footer}
</div></div>
${[...scripts, toggleScript].join("\n")}
</body>
</html>`;
}
