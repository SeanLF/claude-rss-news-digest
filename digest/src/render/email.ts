import { htmlEscape as esc } from "escape-goat";
import { Engine } from "mrml";
import { BUCKET_ORDER, BUCKET_WORD, bucketCounts, collectOutlets, dates, lightTokens, readingTime, roundHalfEven, slugger, storyCounts, type Bucket, type RenderInput, type Story, type ThreadContext } from "./common.js";

// The email, ported from newsroom/src/render_email.py: MJML compiled by mrml, the Rust engine
// mjml-python wraps, here as its WebAssembly build. Colours are the light design tokens; fonts are
// web-safe stacks, since custom web fonts are unreliable in mail clients.
const SERIF = "Georgia, 'Times New Roman', serif";
const SANS = "Arial, Helvetica, sans-serif";
const MONO = "'Courier New', monospace";
const SIDE = "28px";
const MONO_UP = 'letter-spacing="1px" text-transform="uppercase"';

interface Palette { bg: string; ink: string; ink2: string; muted: string; hair: string; accent: string; accentInk: string; bias: Record<Bucket, string> }
function palette(tokensCss: string): Palette {
  const t = lightTokens(tokensCss);
  const need = (k: string) => {
    const v = t[k];
    if (v === undefined) throw new Error(`design token --${k} missing from tokens.css`);
    return v;
  };
  return { bg: need("bg"), ink: need("ink"), ink2: need("ink2"), muted: need("muted"), hair: need("hair"), accent: need("accent"), accentInk: need("accent-ink"), bias: { l: need("bias-l"), c: need("bias-c"), r: need("bias-r") } };
}

// Each mj-text carries only the attributes that differ from the <mj-attributes> defaults, which
// are built from this same map, so the two cannot drift.
type TxtKey = "font-family" | "font-size" | "color" | "line-height" | "align" | "padding";
interface TxtOpts { size?: number; color?: string; font?: string; lh?: string; weight?: string; align?: string; padding?: string; extra?: string }

// A continuing thread's delta, today's verified facts, replaces the summary.
function bodyOf(a: Story): string {
  const delta = (a.thread?.delta ?? "").trim();
  return delta ? esc(delta) : esc(a.summary ?? "");
}

function renderer(p: Palette) {
  const defaults: Record<TxtKey, string> = { "font-family": SERIF, "font-size": "18px", color: p.ink2, "line-height": "1.6", align: "left", padding: "0" };
  const txt = (content: string, { size = 18, color = p.ink2, font = SERIF, lh = "1.6", weight, align = "left", padding = "0", extra = "" }: TxtOpts = {}) => {
    const vals: Record<TxtKey, string> = { "font-family": font, "font-size": `${size}px`, color, "line-height": lh, align, padding };
    let attrs = (Object.keys(vals) as TxtKey[]).filter((k) => vals[k] !== defaults[k]).map((k) => `${k}="${vals[k]}"`).join(" ");
    if (weight) attrs += ` font-weight="${weight}"`;
    if (extra) attrs += ` ${extra}`;
    return `<mj-text ${attrs}>${content}</mj-text>`;
  };
  // Mail clients do not inherit link colour, so every <a> is styled inline.
  const link = (url: string, text: string, color = p.accentInk) => `<a href="${url}" style="color:${color};">${text}</a>`;
  const eyebrow = (label: string) => txt(label, { size: 10, color: p.accentInk, font: MONO, weight: "600", padding: "0 0 4px", extra: MONO_UP });
  const section = (inner: string, { padding = `0 ${SIDE}`, borderLeft = false } = {}) => {
    const col = borderLeft ? `<mj-column padding="0 0 0 16px" border-left="2px solid ${p.accent}">${inner}</mj-column>` : `<mj-column>${inner}</mj-column>`;
    return `<mj-section padding="${padding}">${col}</mj-section>`;
  };
  // The label cell is fixed at 150px and the rule cell has no width: a percentage rule cell starved
  // the label to nothing in Outlook, and a shrinking one char-wrapped it on narrow screens.
  const sectionHeader = (num: string, name: string, gap: boolean) => {
    const label =
      `<span style="font-family:${MONO};font-size:12px;font-weight:600;color:${p.accentInk};letter-spacing:.08em;">${num}</span>` +
      `<span style="font-family:${SANS};font-size:11px;font-weight:700;letter-spacing:.18em;` +
      `text-transform:uppercase;color:${p.ink};">&#160;&#160;${name.replaceAll(" ", "&#160;")}</span>`;
    const rows =
      `<tr><td width="150" style="width:150px;white-space:nowrap;vertical-align:middle;">${label}</td>` +
      `<td style="vertical-align:middle;"><div style="height:1px;line-height:1px;font-size:0;background:${p.ink};">&#160;</div></td></tr>`;
    return `<mj-section padding="${gap ? "48px" : "16px"} ${SIDE} 0"><mj-column><mj-table cellpadding="0" cellspacing="0" width="100%" padding="0">${rows}</mj-table></mj-column></mj-section>`;
  };
  const separator = (brief: boolean) => {
    const [top, bot] = brief ? ["16px", "16px"] : ["32px", "24px"];
    return `<mj-section padding="0 ${SIDE}"><mj-column><mj-spacer height="${top}" /><mj-divider border-width="1px" border-color="${p.hair}" padding="0" /><mj-spacer height="${bot}" /></mj-column></mj-section>`;
  };
  const sources = (a: Story, slug: string, homepage: string) => {
    const outlets = collectOutlets(a);
    if (!outlets.length) return "";
    const counts = bucketCounts(outlets);
    const total = outlets.length;
    const word = total === 1 ? "source" : "sources";
    const present = BUCKET_ORDER.filter((b) => counts[b]);
    const cells = present.map((b) => `<td height="4" width="${roundHalfEven((100 * counts[b]) / total)}%" bgcolor="${p.bias[b]}" style="font-size:0;line-height:0;"></td>`).join("");
    const bar = `<mj-table width="120px" cellpadding="0" cellspacing="0" padding="0 0 8px"><tr>${cells}</tr></mj-table>`;
    const href = homepage ? `${homepage}#${slug}` : `#${slug}`;
    const label =
      `<span style="color:${p.ink2};">${total} ${word}</span> · ${present.map((b) => `${counts[b]} ${BUCKET_WORD[b]}`).join(" · ")} · ` +
      `<a href="${esc(href)}" style="font-family:${SANS};font-size:12px;` +
      `text-transform:none;letter-spacing:0;color:${p.accentInk};">view ${word} online</a>`;
    return `<mj-section padding="16px ${SIDE} 0"><mj-column>${bar}${txt(label, { size: 10, color: p.muted, font: MONO, extra: MONO_UP })}</mj-column></mj-section>`;
  };
  // "Ongoing · day N" sits under the headline like a dateline. A relative thread URL would resolve
  // against the mail client's origin, so only an absolute one becomes a link.
  const threadEyebrow = (thread: ThreadContext, pad = "0 0 12px") => {
    const day = thread.day ?? 0;
    if (day < 2) return "";
    let label = `<span style="color:${p.accentInk};font-weight:600;">Ongoing</span> · day ${day}`;
    const url = thread.url;
    if (typeof url === "string" && (url.startsWith("https://") || url.startsWith("http://")))
      label = `<a href="${esc(url)}" aria-label="Ongoing · day ${day} ↗ how this story developed" style="color:${p.muted};text-decoration:none;">${label} ↗</a>`;
    return txt(label, { size: 11, color: p.muted, font: MONO, padding: pad, extra: MONO_UP });
  };
  const story = (a: Story, slug: string, homepage: string) => {
    const thread = a.thread ?? {};
    const why = esc(a.why_it_matters ?? "").trim();
    const parts = [section(txt(esc(a.headline ?? ""), { size: 25, color: p.ink, lh: "1.24", weight: "600", padding: "0 0 12px" }) + threadEyebrow(thread) + txt(bodyOf(a), { color: p.ink }), { padding: `24px ${SIDE} 0` })];
    if (why) parts.push(section(eyebrow("Why it matters") + txt(why), { padding: `16px ${SIDE} 0`, borderLeft: true }));
    const varies = a.reporting_varies ?? [];
    if (varies.length) parts.push(section(eyebrow("How reporting varies") + varies.map((v) => txt(`<b style="color:${p.ink};">${esc(v.source ?? "")}:</b> ${esc(v.angle ?? "")}`, { padding: "8px 0 0" })).join(""), { padding: `16px ${SIDE} 0` }));
    parts.push(sources(a, slug, homepage));
    return parts.join("");
  };
  const brief = (a: Story, slug: string, homepage: string, first: boolean) => {
    const inner = txt(esc(a.headline ?? ""), { size: 20, color: p.ink, lh: "1.3", weight: "600", padding: "0 0 4px" }) + threadEyebrow(a.thread ?? {}, "0 0 4px") + txt(bodyOf(a));
    // The first brief needs a gap below the section header; later ones get it from the separator.
    return section(inner, { padding: first ? `20px ${SIDE} 0` : `0 ${SIDE}` }) + sources(a, slug, homepage);
  };
  return { defaults, txt, link, section, sectionHeader, separator, story, brief };
}

export function renderEmail({ selections, now, issueNo, env, assets }: RenderInput, unsubscribeUrl = "{{{RESEND_UNSUBSCRIBE_URL}}}"): string {
  const p = palette(assets.tokens);
  const { defaults, txt, link, section, sectionHeader, separator, story, brief } = renderer(p);
  const d = dates(now);
  const homepage = env.digestDomain ? `https://${env.digestDomain}/issues/${d.iso}` : "";
  const archive = env.archiveUrl;
  const { authorName, authorUrl } = env;
  const slug = slugger();

  const body: string[] = [];
  body.push(
    homepage
      ? section(
          txt(`<a href="${homepage}" style="color:${p.muted};text-decoration:none;">View in browser</a> <span style="color:${p.hair};">·</span> <a href="${homepage}/translate" style="color:${p.muted};text-decoration:none;"><span>文A</span> Translate</a>`, { size: 10, color: p.muted, font: MONO, align: "center", extra: MONO_UP }),
          { padding: `24px ${SIDE} 0` },
        )
      : "",
  );
  const brand = txt(`Sean&#39;s Daily <span style="color:${p.accentInk};">Digest</span>`, { size: 27, color: p.ink, weight: "600", lh: "1" });
  const issue = txt(`${issueNo === null ? "" : `No. ${issueNo}<br/>`}Filed ${d.filed}`, { size: 10, color: p.muted, font: MONO, align: "right", lh: "1.55", extra: MONO_UP });
  body.push(`<mj-section padding="20px ${SIDE} 0"><mj-column width="60%" vertical-align="bottom">${brand}</mj-column><mj-column width="40%" vertical-align="bottom">${issue}</mj-column></mj-section>`);
  body.push(section(txt(`<span style="color:${p.accent};">■</span> ${d.long} &nbsp;/&nbsp; ${readingTime(selections)} &nbsp;/&nbsp; ${storyCounts(selections)}`, { size: 11, color: p.muted, font: MONO, padding: "6px 0 12px", extra: MONO_UP })));
  body.push(`<mj-section padding="0 ${SIDE}"><mj-column><mj-divider border-width="2px" border-color="${p.ink}" padding="0" /></mj-column></mj-section>`);
  body.push(
    section(
      txt(
        `<span style="font-family:${MONO};color:${p.accentInk};font-weight:600;font-size:10px;letter-spacing:1px;">AI-WRITTEN</span>` +
          "&#160;&#160;Written by Claude, an assistant that can make mistakes - verify anything important against the linked sources. " +
          `Political leanings from <a href="${archive}/sources" style="color:${p.accentInk};">independent media assessors</a>.`,
        { size: 12, font: SANS },
      ),
      { padding: `12px ${SIDE}` },
    ),
  );
  for (const [label, num, key, isBrief] of [["Must Know", "01", "must_know", false], ["Should Know", "02", "should_know", true]] as const) {
    const items = selections[key];
    if (!items.length) continue;
    body.push(sectionHeader(num, label, key === "should_know"));
    items.forEach((a, i) => {
      const s = slug(a.headline ?? "");
      if (i > 0) body.push(separator(isBrief));
      body.push(isBrief ? brief(a, s, homepage, i === 0) : story(a, s, homepage));
    });
  }
  const notCovered = (selections.not_covered_blurb ?? "").trim();
  let nav = `${link(archive, "Past digests")} · ${link(`${archive}/sources`, "Sources")}`;
  if (authorUrl) nav += ` · ${link(`${authorUrl.replace(/\/+$/, "")}/privacy`, "Privacy")}`;
  nav += ` · ${link(unsubscribeUrl, "Unsubscribe")}`;
  const plug = authorName && authorUrl ? `Made by ${link(esc(authorUrl), esc(authorName))}` : "";
  const footer =
    '<mj-spacer height="48px" />' +
    `<mj-divider border-width="1px" border-color="${p.hair}" padding="0" />` +
    '<mj-spacer height="16px" />' +
    txt(nav, { size: 12, color: p.muted, font: SANS, lh: "1.7" }) +
    txt("Reply to this email with feedback.", { size: 12, color: p.muted, font: SANS, padding: "8px 0 0" }) +
    (notCovered ? txt(`Not covered today: ${esc(notCovered)}`, { size: 11, color: p.muted, font: SANS, padding: "8px 0 0" }) : "") +
    (plug ? txt(plug, { size: 11, color: p.muted, font: SANS, padding: "8px 0 0" }) : "") +
    txt(d.generated, { size: 11, color: p.muted, font: SANS, padding: "8px 0 0" });
  body.push(`<mj-section padding="0 ${SIDE}"><mj-column>${footer}</mj-column></mj-section>`);

  const textDefaults = (Object.keys(defaults) as TxtKey[]).filter((k) => k !== "font-family").map((k) => `${k}="${defaults[k]}"`).join(" ");
  const mjml =
    "<mjml><mj-head>" +
    `<mj-attributes><mj-all font-family="${SERIF}" />` +
    `<mj-text ${textDefaults} /></mj-attributes>` +
    `<mj-preview>${esc(selections.preheader ?? "")}</mj-preview>` +
    "</mj-head>" +
    `<mj-body background-color="${p.bg}" width="660px">${body.join("")}</mj-body></mjml>`;
  const result = new Engine().toHtml(mjml);
  // A compile error or an empty result must never reach a send.
  if (result.type === "error") throw new Error(`MJML compile error, refusing to send: ${result.message}`);
  if (!result.content.trim()) throw new Error("render_email produced empty HTML; refusing to send");
  return result.content;
}
