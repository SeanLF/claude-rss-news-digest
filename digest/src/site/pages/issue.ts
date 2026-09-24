import { fontFace } from "../assets.js";
import { ogImageUrl } from "../config.js";
import { hiddenPointer, markdownLinkTag } from "../markdown.js";
import { escapeHtml } from "../text.js";
import { digestNavCss, proxyTranslateHideScript, reducedMotionCss, skipLinkCss, toggleJs } from "./blobs.js";
import { NO_FLASH_JS, type PageCtx, TOGGLE_BTN, ogImageTags, topbar, translatePill } from "./chrome.js";

// An issue's web page: the HTML the pipeline rendered, with the site's chrome injected at fixed places
// (circulation's get_digest). The needles are the template's own markup, held by issue.test.ts against
// newsroom/templates/digest-template.html.

export const NEEDLES = { head: "</head>", body: "<body>", paper: '<div class="paper">', footerMeta: '<p class="footer-meta">', footer: "</footer>", bodyEnd: "</body>" } as const;

const FAVICON =
  "<link rel=\"icon\" type=\"image/svg+xml\" href=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23c45a3b'/%3E%3Cline x1='8' y1='10' x2='24' y2='10' stroke='white' stroke-width='2.5' stroke-linecap='round'/%3E%3Cline x1='8' y1='16' x2='20' y2='16' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.7'/%3E%3Cline x1='8' y1='22' x2='16' y2='22' stroke='white' stroke-width='2.5' stroke-linecap='round' opacity='.4'/%3E%3C/svg%3E\">";
const SKIP_LINK = '<a href="#main" class="skip-link">Skip to content</a>';

// The top bar above the masthead, inside .paper; the Translate pill points at this issue's translation.
const navHtml = (date: string): string =>
  topbar(
    [
      ["/", "&larr; Archive"],
      ["/sources", "Sources"],
      ["/threads", "Threads"],
      ["/stats", "Stats"],
    ],
    `${translatePill(`/issues/${date}/translate`)}${TOGGLE_BTN}`,
  );

const feedbackHtml = (date: string, email: string | undefined): string =>
  email ? `<p class="footer-feedback">Got feedback or a suggestion? <a href="mailto:${escapeHtml(email)}?subject=Digest%20feedback%20-%20${date}">Send a note &rarr;</a></p>` : "";

// Replaces the first `needle`, or records it as missed: the stored HTML drifted from the template and
// that piece of chrome is gone from the page.
function inject(html: string, needle: string, replacement: string, missed: string[]): string {
  const i = html.indexOf(needle);
  if (i < 0) {
    missed.push(needle);
    return html;
  }
  return html.slice(0, i) + replacement + html.slice(i + needle.length);
}

// The page, with every needle it failed to find. The feedback line's needles are looked for only when
// a contact address is configured.
export function renderIssue(ctx: PageCtx, date: string, stored: { html: string; preheader: string }, mdAbs: string): { html: string; missed: string[] } {
  const missed: string[] = [];
  const { cfg } = ctx;
  const title = escapeHtml(`${cfg.digestName} – ${date}`);
  const description = escapeHtml(stored.preheader);
  const canonical = cfg.digestDomain ? `https://${cfg.digestDomain}/issues/${date}` : "";
  const og = `<meta property="og:title" content="${title}">
  <meta property="og:description" content="${description}">
  <meta property="og:type" content="article">
  <meta property="og:url" content="${canonical}">
  <meta property="og:site_name" content="${escapeHtml(cfg.digestName)}">
  <meta name="description" content="${description}">
  ${ogImageTags(ogImageUrl(cfg))}`;
  // color-scheme: the web archive has a real light/dark toggle, unlike the light-only email.
  const headInject = `<meta name="color-scheme" content="light dark">
  ${FAVICON}
  ${og}
  ${markdownLinkTag(`/issues/${date}.md`)}
  <script>${proxyTranslateHideScript}</script>
  <script>${NO_FLASH_JS}</script>`;
  // The pipeline's own <style> blocks are hashed into the CSP with the site's; a <script> in the stored
  // HTML is not one the site wrote, so the CSP refuses it.
  let html = inject(
    stored.html,
    NEEDLES.head,
    `${headInject}\n<style>${digestNavCss}</style>\n<style>${fontFace(ctx.assets.fontUrl)}\n${skipLinkCss}\n${reducedMotionCss}</style></head>`,
    missed,
  );
  html = inject(html, NEEDLES.body, `<body>${SKIP_LINK}${hiddenPointer(mdAbs)}`, missed);
  html = inject(html, NEEDLES.paper, `${NEEDLES.paper}${navHtml(date)}`, missed);
  const feedback = feedbackHtml(date, cfg.contactEmail);
  if (feedback) {
    html = html.includes(NEEDLES.footerMeta)
      ? inject(html, NEEDLES.footerMeta, `${feedback}\n    ${NEEDLES.footerMeta}`, missed)
      : inject(html, NEEDLES.footer, `${feedback}\n  </footer>`, missed);
  }
  html = inject(html, NEEDLES.bodyEnd, `<script>${toggleJs}</script></body>`, missed);
  return { html, missed };
}

// The page as served. A miss still serves what rendered, and is logged at error level: `make
// check-injections` finds every stored issue that has one.
export function issuePage(ctx: PageCtx, date: string, stored: { html: string; preheader: string }, mdAbs: string): string {
  const { html, missed } = renderIssue(ctx, date, stored, mdAbs);
  for (const needle of missed) console.error(JSON.stringify({ site: "issue", level: "error", date, needle, error: "web injection missed; the stored HTML drifted from the template" }));
  return html;
}
