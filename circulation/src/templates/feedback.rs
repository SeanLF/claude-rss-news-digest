//! Feedback page — a warm mailto CTA. The per-story up/down vote was removed product-wide, so this
//! is a static, read-only page (kept because already-sent emails link to `/feedback`). Shared frame
//! comes from [`super::chrome`]; the content sits in a narrow measure, left-aligned in the wide column.

use super::chrome;
use super::digest::og_image_tags;

pub struct FeedbackParams<'a> {
    pub title: &'a str,
    pub brand_html: &'a str,
    pub home_url: &'a str,
    pub canonical_url: &'a str,
    pub feed_url: &'a str,
    pub image_url: &'a str,
    pub font_url: &'a str,
    pub topbar_html: &'a str,
    pub footer_html: &'a str,
    /// The address a `mailto:` CTA targets (the digest's `RESEND_FROM`). Absent → CTA is reply-only.
    pub mailto: Option<&'a str>,
    /// The "reply to today's issue" link target (the stable `/today` route).
    pub today_url: &'a str,
}

const FEEDBACK_CSS: &str = r#"
.narrow{max-width:620px;}
.lede{font-family:var(--serif); font-size:19px; color:var(--ink2); line-height:1.55; margin:20px 0 0;}
.body{font-family:var(--serif); font-size:16px; color:var(--muted); line-height:1.65; margin:16px 0 0;}
.cta{display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:28px;}
.cta .btn{font-size:14px; padding:12px 22px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;}
.cta .alt{font-family:var(--serif); font-size:14px; color:var(--muted);}
.cta .alt a{text-decoration:none; background-image:linear-gradient(var(--accent-ink),var(--accent-ink));
  background-size:100% 1px; background-repeat:no-repeat; background-position:0 100%;}
.helps{margin-top:36px; border-top:1px solid var(--line); padding-top:20px;}
.helps h2{font-family:var(--sans); font-weight:700; font-size:11px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink); margin:0 0 12px;}
.helps ul{margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:9px;}
.helps li{font-family:var(--serif); font-size:15px; color:var(--ink2); line-height:1.5; padding-left:18px; position:relative;}
.helps li::before{content:"—"; position:absolute; left:0; color:var(--accent-ink);}
@media (max-width:560px){ .cta .btn{width:100%; justify-content:center;} }
"#;

const HELPS: [&str; 4] = [
    "A story that felt one-sided, or a source you'd trust more.",
    "Something important the digest missed entirely.",
    "Whether the length and cadence work for your mornings.",
    "Anything that made you want to unsubscribe — those are the most valuable of all.",
];

pub fn render_feedback(p: &FeedbackParams) -> String {
    let head = chrome::page_head(
        p.title,
        "Tell me what you think — feedback goes straight to a human inbox.",
        p.canonical_url,
        p.feed_url,
        &og_image_tags(p.image_url),
        p.font_url,
        FEEDBACK_CSS,
    );

    // The mailto button only appears when a feedback address is configured; the reply-to-today link
    // is always offered as the low-friction path.
    let mailto_btn = match p.mailto {
        Some(email) => format!(
            r#"<a class="btn primary" href="mailto:{email}?subject=Digest%20feedback">&#9993; Email your feedback</a>"#
        ),
        None => String::new(),
    };
    let alt = format!(
        r#"<span class="alt">or just <a href="{}">reply to today's issue &rarr;</a></span>"#,
        p.today_url
    );
    let helps: String = HELPS.iter().map(|h| format!("<li>{h}</li>")).collect();

    format!(
        r#"{head}
<body>
{skip}
<div class="wrap"><div class="col">
    {topbar}
    <main id="main" class="narrow">
      <a class="brandmark" href="{home}">{brand}</a>
      <h1 class="h1">Tell me what you <em>think</em></h1>
      <p class="lede">This digest is a work in progress, and the best version of it is shaped by the people who read it every morning.</p>
      <p class="body">Reply to any issue in your inbox, or send a note directly — a real person reads every one, and it genuinely steers what gets built next.</p>
      <div class="cta">{mailto_btn}{alt}</div>
      <div class="helps">
        <h2>Especially useful to hear</h2>
        <ul>{helps}</ul>
      </div>
    </main>
    {footer}
</div></div>
{toggle_js}
</body>
</html>"#,
        skip = chrome::SKIP_HTML,
        topbar = p.topbar_html,
        home = p.home_url,
        brand = p.brand_html,
        footer = p.footer_html,
        toggle_js = chrome::TOGGLE_JS,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(mailto: Option<&'static str>) -> FeedbackParams<'static> {
        FeedbackParams {
            title: "Feedback",
            brand_html: "News <em>Digest</em>",
            home_url: "/",
            canonical_url: "https://example.com/feedback",
            feed_url: "/feed.xml",
            image_url: "https://example.com/og.png",
            font_url: "/assets/fonts/x.woff2",
            topbar_html: "<div class=\"topbar\"></div>",
            footer_html: "<footer></footer>",
            mailto,
            today_url: "/today",
        }
    }

    #[test]
    fn renders_mailto_cta_and_helps_when_email_configured() {
        let html = render_feedback(&params(Some("hi@example.com")));
        assert!(html.contains(r#"href="mailto:hi@example.com?subject=Digest%20feedback""#));
        assert!(html.contains("Especially useful to hear"));
        assert!(html.contains("those are the most valuable of all"));
        assert!(html.contains(r#"<a href="/today">reply to today's issue"#));
        assert_eq!(html.matches("<h1").count(), 1);
        assert!(html.contains(r#"<main id="main" class="narrow">"#));
    }

    #[test]
    fn omits_mailto_button_when_no_email_but_keeps_reply_link() {
        let html = render_feedback(&params(None));
        assert!(!html.contains("mailto:"));
        assert!(html.contains(r#"reply to today's issue"#));
    }
}
