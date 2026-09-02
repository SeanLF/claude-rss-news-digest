//! LLM-visibility plumbing: serve the same content as clean Markdown for AI agents and
//! `Accept: text/markdown` clients, plus the `Content-Signal` / `/llms.txt` discovery files.
//!
//! Single source of truth: the Markdown is *derived* at request time from the stored HTML
//! (`digests.html` — the one canonical per-issue artifact) via [`issue_markdown`], never a
//! second stored copy. This lives entirely in the read-only circulation server, so nothing
//! here touches the newsroom send pipeline. Companion to the Evil Martians "make your website
//! visible to LLMs" conventions (`.md` routes + `Link`/`<link>` alternates + Accept
//! negotiation + Cloudflare `Content-Signal`).

use htmd::element_handler::{HandlerResult, Handlers};
use htmd::{Element, HtmlToMarkdown};

use crate::archive::{IndexMeta, IssueRow};

/// The result of `Accept:` content negotiation between the HTML and Markdown representations.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Negotiated {
    /// Serve HTML (the default: no `Accept`, a browser wildcard, or an explicit HTML preference).
    Html,
    /// Serve Markdown (the client preferred, or explicitly tied on, `text/markdown`).
    Markdown,
    /// Neither HTML nor Markdown is acceptable — the caller returns `406 Not Acceptable`.
    NotAcceptable,
}

/// Float tolerance for q-value ties (q has at most 3 decimals per RFC 9110).
const EPS: f32 = 1e-4;

/// Negotiate HTML vs Markdown from an `Accept` header, per the four non-negotiables:
///
/// 1. **Compare q-values**, never substring-match (`text/html, text/markdown;q=0.5` → HTML).
/// 2. **Resolve ties to Markdown only when `text/markdown` is explicitly named** — a coding
///    agent's `text/markdown, text/html` (both q=1) → Markdown, but a browser's wildcard tie
///    (`*/*`) → HTML.
/// 3. **`406`** when neither is acceptable (callers skip this for an explicit `.md` URL).
/// 4. Absent/empty `Accept` → HTML (browsers and bare `curl` send `*/*` or nothing).
pub fn negotiate(accept: Option<&str>) -> Negotiated {
    let accept = match accept {
        Some(a) if !a.trim().is_empty() => a,
        _ => return Negotiated::Html,
    };

    // Highest q seen for each media type we care about (explicit types kept apart from wildcards,
    // because the tie rule only rewards an *explicit* text/markdown).
    let mut html_explicit: Option<f32> = None;
    let mut md_explicit: Option<f32> = None;
    let mut text_wild: Option<f32> = None;
    let mut any_wild: Option<f32> = None;

    for part in accept.split(',') {
        let mut segs = part.split(';').map(str::trim);
        let media = match segs.next() {
            Some(m) if !m.is_empty() => m.to_ascii_lowercase(),
            _ => continue,
        };
        let mut q = 1.0f32;
        for param in segs {
            if let Some(v) = param.strip_prefix("q=") {
                // A present q overrides the 1.0 default. Clamp valid values to [0,1] so a bogus
                // q=2 can't outrank a real q=1; treat an unparseable/non-finite q as 0 so a
                // garbled preference can't win a tie (never default a broken q to the strongest).
                q = v
                    .trim()
                    .parse::<f32>()
                    .ok()
                    .filter(|p| p.is_finite())
                    .map_or(0.0, |p| p.clamp(0.0, 1.0));
            }
        }
        let slot = match media.as_str() {
            "text/html" => &mut html_explicit,
            "text/markdown" => &mut md_explicit,
            "text/*" => &mut text_wild,
            "*/*" => &mut any_wild,
            _ => continue,
        };
        if slot.is_none_or(|cur| q > cur) {
            *slot = Some(q);
        }
    }

    // Effective acceptability of each representation folds in the wildcards.
    let best = |explicit: Option<f32>| -> Option<f32> {
        [explicit, text_wild, any_wild]
            .into_iter()
            .flatten()
            .fold(None, |acc: Option<f32>, q| {
                Some(acc.map_or(q, |a| a.max(q)))
            })
    };
    let html_q = best(html_explicit);
    let md_q = best(md_explicit);

    match (html_q, md_q) {
        (None, None) => Negotiated::NotAcceptable,
        (Some(_), None) => Negotiated::Html,
        (None, Some(_)) => Negotiated::Markdown,
        (Some(h), Some(m)) => {
            if m > h + EPS {
                Negotiated::Markdown
            } else if h > m + EPS {
                Negotiated::Html
            } else if md_explicit.is_some_and(|q| (q - m).abs() < EPS) {
                // Tie resolved to Markdown only because the client explicitly named it.
                Negotiated::Markdown
            } else {
                Negotiated::Html
            }
        }
    }
}

/// Isolate the editorial `<main>…</main>` region of a stored digest blob, so the derived
/// Markdown carries the briefing itself and not the masthead/footer chrome. Falls back to the
/// whole document if the marker is missing (older blobs).
fn extract_main(html: &str) -> &str {
    if let Some(start) = html.find("<main") {
        let rest = &html[start..];
        if let Some(end) = rest.find("</main>") {
            return &rest[..end + "</main>".len()];
        }
        // `<main>` opened but never closed -- a truncated/corrupt blob. Warn (chrome will leak
        // into the derived Markdown) rather than silently shipping the masthead and footer.
        tracing::warn!("extract_main: <main> without a closing </main>; blob may be truncated");
    }
    html
}

/// Whether an element's `class` attribute carries `token` as one of its space-separated names.
fn has_class(element: &Element, token: &str) -> bool {
    element
        .attrs
        .iter()
        .filter(|a| &*a.name.local == "class")
        .any(|a| a.value.split_whitespace().any(|t| t == token))
}

/// The digest's own chrome, keyed on the class names `render.py` and the template emit. Three
/// things are decoration a reader's eye skips and a text reader trips over: the "AI-written"
/// pill (which ran into the sentence after it), the `01`/`02` section numerals, and the
/// copy-link anchor after every headline (an empty `[](#slug)`). The "Why it matters" label is
/// a styled span that arrived as a bare line; as bold it reads as the label it is. Everything
/// else falls back to htmd's own handler, so this never re-implements link or span rendering.
fn span_handler(handlers: &dyn Handlers, element: Element) -> Option<HandlerResult> {
    if has_class(&element, "tag") || has_class(&element, "num") {
        return None;
    }
    if has_class(&element, "lbl") {
        let label = handlers.walk_children(element.node).content;
        let label = label.trim();
        if !label.is_empty() {
            return Some(format!("**{label}**").into());
        }
    }
    handlers.fallback(element)
}

fn anchor_handler(handlers: &dyn Handlers, element: Element) -> Option<HandlerResult> {
    if has_class(&element, "anchor") {
        return None;
    }
    handlers.fallback(element)
}

/// Convert the stored digest HTML blob for `date` into a standalone Markdown document. The blob
/// is the single source; this derives a view of it. `script`/`style` are dropped so no inlined
/// JS/CSS leaks into the text, and the page chrome that only makes sense with its CSS is left
/// out (see `span_handler`). Returns `None` (with a logged error) when the blob derives an
/// empty body or `htmd` fails -- the caller then fails loud rather than serve a hollow,
/// title-only document to an agent.
pub fn issue_markdown(html_blob: &str, digest_name: &str, date: &str) -> Option<String> {
    let main = extract_main(html_blob);
    let converter = HtmlToMarkdown::builder()
        .skip_tags(vec!["script", "style"])
        .add_handler(vec!["span"], span_handler)
        .add_handler(vec!["a"], anchor_handler)
        .build();
    let body = match converter.convert(main) {
        Ok(b) => b,
        Err(e) => {
            tracing::error!(%date, blob_len = html_blob.len(), error = %e, "issue markdown: htmd conversion failed");
            return None;
        }
    };
    let body = body.trim();
    if body.is_empty() {
        tracing::error!(%date, blob_len = html_blob.len(), "issue markdown: derived body is empty");
        return None;
    }
    Some(format!("# {digest_name} — {date}\n\n{body}\n"))
}

/// Prefix a root-relative path with `base_url` when a domain is configured, else leave it
/// root-relative. Absolute links are friendlier for agents that ingest `/llms.txt` or `.md`.
fn abs(base_url: &str, path: &str) -> String {
    if base_url.is_empty() {
        path.to_string()
    } else {
        format!("{}{path}", base_url.trim_end_matches('/'))
    }
}

/// The archive index as Markdown: a curated running order of every issue, each linking to its
/// `.md`. Built from the same `IndexMeta` + `IssueRow`s the HTML index renders from.
pub fn index_markdown(
    digest_name: &str,
    meta: &IndexMeta,
    issues: &[IssueRow],
    base_url: &str,
) -> String {
    let mut out = format!("# {digest_name}\n\n");
    out.push_str(
        "> An automated daily news briefing: geopolitics, tech, and privacy, from sources across \
         the political spectrum, each labelled by bias and factuality. Curated and written by \
         Claude, fact-checked against its sources, filed by a human.\n\n",
    );
    if let (Some(first), Some(newest)) = (&meta.first_date, &meta.newest_date) {
        let issue_s = if meta.total == 1 { "" } else { "s" };
        let story_suffix = if meta.total_stories == 1 { "y" } else { "ies" };
        out.push_str(&format!(
            "{} issue{issue_s} from {first} to {newest} · {} stor{story_suffix}.\n\n",
            meta.total, meta.total_stories
        ));
    }
    out.push_str("## Issues\n\n");
    for row in issues {
        let link = abs(base_url, &format!("/issues/{}.md", row.date));
        let pre = row.preheader.trim();
        if pre.is_empty() {
            out.push_str(&format!("- [{}]({link})\n", row.date));
        } else {
            out.push_str(&format!(
                "- [{}]({link}): {pre} ({} sources)\n",
                row.date, row.source_count
            ));
        }
    }
    out
}

/// `<link rel="alternate" type="text/markdown" href="…">` for a page's `<head>` — caught by
/// DOM-parsing crawlers.
pub fn markdown_link_tag(md_url: &str) -> String {
    format!(r#"<link rel="alternate" type="text/markdown" href="{md_url}">"#)
}

/// HTTP `Link` header value advertising a `text/markdown` alternate — caught by headless
/// fetchers that never read the body.
pub fn markdown_link_header(md_url: &str) -> String {
    format!(r#"<{md_url}>; rel="alternate"; type="text/markdown""#)
}

/// HTTP `Link` header value pointing a `.md` response back at its HTML representation, so a
/// client that lands on either form can find the other.
pub fn html_link_header(html_url: &str) -> String {
    format!(r#"<{html_url}>; rel="alternate"; type="text/html""#)
}

/// A visually-hidden, screen-reader-hidden pointer to the Markdown URL, for the "human pastes
/// this URL into ChatGPT" flow. Inline-styled so it needs no shared CSS and can be injected into
/// the stored digest blob.
pub fn hidden_pointer(md_url: &str) -> String {
    format!(
        r#"<div aria-hidden="true" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;">A Markdown version of this page is available at {md_url}.</div>"#
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- Accept negotiation: the four non-negotiables ----

    #[test]
    fn absent_or_empty_accept_serves_html() {
        assert_eq!(negotiate(None), Negotiated::Html);
        assert_eq!(negotiate(Some("")), Negotiated::Html);
        assert_eq!(negotiate(Some("   ")), Negotiated::Html);
    }

    #[test]
    fn bare_curl_and_browser_wildcards_serve_html() {
        // bare `curl` sends */*
        assert_eq!(negotiate(Some("*/*")), Negotiated::Html);
        // typical browser Accept
        assert_eq!(
            negotiate(Some(
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"
            )),
            Negotiated::Html
        );
    }

    #[test]
    fn q_values_are_compared_not_substring_matched() {
        // Prefers HTML: must NOT ship markdown just because the string contains "text/markdown".
        assert_eq!(
            negotiate(Some("text/html, text/markdown;q=0.5")),
            Negotiated::Html
        );
        // Prefers markdown explicitly.
        assert_eq!(
            negotiate(Some("text/markdown;q=0.9, text/html;q=0.5")),
            Negotiated::Markdown
        );
    }

    #[test]
    fn coding_agent_tie_resolves_to_markdown_when_explicitly_named() {
        // Claude Code / Cursor commonly send both at q=1 — a strict `>` would wrongly pick HTML.
        assert_eq!(
            negotiate(Some("text/markdown, text/html")),
            Negotiated::Markdown
        );
        assert_eq!(negotiate(Some("text/markdown")), Negotiated::Markdown);
    }

    #[test]
    fn wildcard_tie_does_not_flip_to_markdown() {
        // */* ties HTML and Markdown via the wildcard, but markdown wasn't named -> HTML.
        assert_eq!(negotiate(Some("*/*")), Negotiated::Html);
        assert_eq!(negotiate(Some("text/*")), Negotiated::Html);
    }

    #[test]
    fn neither_acceptable_is_406() {
        assert_eq!(
            negotiate(Some("application/json")),
            Negotiated::NotAcceptable
        );
        assert_eq!(
            negotiate(Some("image/png, application/pdf")),
            Negotiated::NotAcceptable
        );
    }

    // ---- Markdown derivation ----

    #[test]
    fn issue_markdown_converts_only_the_main_region() {
        let blob = r#"<!DOCTYPE html><html><head><style>.x{color:red}</style></head>
<body><div class="paper"><table class="masthead"><tr><td>MASTHEAD JUNK</td></tr></table>
<main id="main"><h2>Must Know</h2><p>A thing <a href="https://e.com">happened</a>.</p>
<script>alert(1)</script></main>
<footer><nav>FOOTER JUNK</nav></footer></div></body></html>"#;
        let md = issue_markdown(blob, "News Digest", "2026-07-03").expect("real blob derives md");
        assert!(md.starts_with("# News Digest — 2026-07-03\n"));
        assert!(md.contains("Must Know"));
        assert!(md.contains("[happened](https://e.com)"));
        // chrome and inlined script/style must not leak into the derived Markdown
        assert!(!md.contains("MASTHEAD JUNK"));
        assert!(!md.contains("FOOTER JUNK"));
        assert!(!md.contains("alert(1)"));
        assert!(!md.contains("color:red"));
    }

    #[test]
    fn issue_markdown_drops_chrome_and_labels_the_why() {
        // The shapes render.py and the template emit, verbatim in class names.
        let html = concat!(
            "<html><body><main>",
            r##"<p class="notice"><span class="tag">AI-written</span>Written by Claude, an assistant. "##,
            r##"Leanings from <a href="https://digest.example/sources">independent assessors</a>.</p>"##,
            r##"<section><div class="section"><span class="num" aria-hidden="true">01</span>"##,
            r##"<h2 class="name" id="s-mk">Must Know</h2><span class="rule" aria-hidden="true"></span></div>"##,
            r##"<article><h3 id="ceasefire-holds">Ceasefire holds"##,
            r##"<a class="anchor" href="#ceasefire-holds" aria-label="Copy link: Ceasefire holds"></a></h3>"##,
            "<p>A summary of the day.</p>",
            r##"<div class="why"><span class="lbl">Why it matters</span><p>Because it does.</p></div>"##,
            r##"<p><span class="src">Reuters</span> <a href="https://example.com/a?x=(1)">1</a></p>"##,
            "</article></section></main></body></html>"
        );
        let md = issue_markdown(html, "Test Digest", "2026-09-02").expect("converts");
        assert!(!md.contains("AI-written"), "{md}");
        assert!(md.contains("Written by Claude, an assistant."), "{md}");
        assert!(!md.contains("\n01\n"), "{md}");
        assert!(md.contains("## Must Know"), "{md}");
        assert!(md.contains("### Ceasefire holds\n"), "{md}");
        assert!(!md.contains("[](#"), "{md}");
        assert!(md.contains("**Why it matters**"), "{md}");
        assert!(md.contains("Because it does."), "{md}");
        // Ordinary links and spans still render through htmd's own handlers.
        assert!(
            md.contains("[independent assessors](https://digest.example/sources)"),
            "{md}"
        );
        assert!(md.contains("Reuters"), "{md}");
        assert!(md.contains("[1](https://example.com/a?x=\\(1\\))"), "{md}");
    }

    #[test]
    fn issue_markdown_falls_back_to_whole_doc_without_main() {
        let blob = "<html><body><h2>Legacy</h2><p>No main tag here.</p></body></html>";
        let md = issue_markdown(blob, "D", "2026-01-01").expect("legacy blob derives md");
        assert!(md.contains("Legacy"));
        assert!(md.contains("No main tag here."));
    }

    #[test]
    fn issue_markdown_returns_none_on_empty_derivation() {
        // Empty/whitespace blob, or a <main> containing only skipped tags, yields no body ->
        // None, so the handler fails loud instead of serving a hollow title-only 200.
        assert_eq!(issue_markdown("", "D", "2026-01-01"), None);
        assert_eq!(issue_markdown("    ", "D", "2026-01-01"), None);
        assert_eq!(
            issue_markdown("<main><script>x()</script></main>", "D", "2026-01-01"),
            None
        );
    }

    #[test]
    fn negotiate_ignores_malformed_q_and_clamps_out_of_range() {
        // A garbled q must not silently outrank a real preference.
        assert_eq!(
            negotiate(Some("text/html, text/markdown;q=abc")),
            Negotiated::Html,
            "garbled q on markdown must not beat an explicit html preference"
        );
        // q=2 is clamped to 1, so it ties (not beats) html; wildcard-free explicit tie on md wins.
        assert_eq!(
            negotiate(Some("text/markdown;q=2.0, text/html;q=1.0")),
            Negotiated::Markdown
        );
        // A clamped q=2 does not outrank an equal, explicitly-named html.
        assert_eq!(
            negotiate(Some("text/html, text/markdown;q=5")),
            Negotiated::Markdown,
            "both clamp to 1.0 -> explicit-markdown tie rule applies"
        );
    }

    #[test]
    fn index_markdown_lists_issues_with_absolute_md_links() {
        let meta = IndexMeta {
            total: 2,
            first_date: Some("2026-06-01".into()),
            newest_date: Some("2026-06-02".into()),
            total_stories: 40,
        };
        let issues = vec![
            IssueRow {
                date: "2026-06-02".into(),
                issue_no: 2,
                preheader: "Newest day".into(),
                source_count: 24,
                bias_l: 8,
                bias_c: 10,
                bias_r: 6,
                must: 5,
                should: 10,
                is_month_start: true,
            },
            IssueRow {
                date: "2026-06-01".into(),
                issue_no: 1,
                preheader: "".into(),
                source_count: 0,
                bias_l: 0,
                bias_c: 0,
                bias_r: 0,
                must: 0,
                should: 0,
                is_month_start: false,
            },
        ];
        let md = index_markdown("News Digest", &meta, &issues, "https://example.com");
        assert!(md.starts_with("# News Digest\n"));
        assert!(md.contains("2 issues from 2026-06-01 to 2026-06-02 · 40 stories."));
        assert!(md.contains(
            "- [2026-06-02](https://example.com/issues/2026-06-02.md): Newest day (24 sources)"
        ));
        // an empty preheader degrades to a bare dated link
        assert!(md.contains("- [2026-06-01](https://example.com/issues/2026-06-01.md)\n"));
    }

    #[test]
    fn link_helpers_emit_rfc_shapes() {
        assert_eq!(
            markdown_link_tag("/issues/2026-07-03.md"),
            r#"<link rel="alternate" type="text/markdown" href="/issues/2026-07-03.md">"#
        );
        assert_eq!(
            markdown_link_header("/issues/2026-07-03.md"),
            r#"</issues/2026-07-03.md>; rel="alternate"; type="text/markdown""#
        );
        assert_eq!(
            html_link_header("/issues/2026-07-03"),
            r#"</issues/2026-07-03>; rel="alternate"; type="text/html""#
        );
        assert!(hidden_pointer("https://x/y.md").contains("aria-hidden=\"true\""));
        assert!(hidden_pointer("https://x/y.md").contains("https://x/y.md"));
    }
}
